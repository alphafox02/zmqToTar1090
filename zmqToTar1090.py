#!/usr/bin/env python3
## author: l0g
## borrowed code from https://github.com/alphafox02/

import zmq
import json
import argparse
import signal
import sys
import datetime
import time
import logging
from collections import deque

logger = logging.getLogger(__name__)

def iso_timestamp_now() -> str:
    """Return current time as an ISO8601 string with a 'Z' for UTC."""
    # e.g. "2024-07-28T20:13:15.123456Z"
    return datetime.datetime.utcnow().isoformat(timespec='milliseconds') + 'Z'

class Drone:
    """Represents a drone and its telemetry data."""
    def __init__(self, id: str, lat: float, lon: float, speed: float, vspeed: float,
                 alt: float, height: float, pilot_lat: float, pilot_lon: float,
                 description: str, time_str: str):
        self.id = id
        self.lat = lat
        self.lon = lon
        self.speed = speed
        self.vspeed = vspeed
        self.alt = alt
        self.height = height
        self.pilot_lat = pilot_lat
        self.pilot_lon = pilot_lon
        self.description = description

        # 'time' is used so tar1090 can parse a valid date/time and compute 'seen'.
        # We'll store an ISO8601 string each time we see a new message.
        self.time = time_str

    def update(self, lat: float, lon: float, speed: float, vspeed: float, alt: float,
               height: float, pilot_lat: float, pilot_lon: float, description: str, time_str: str):
        """Updates the drone's telemetry data and last seen time."""
        self.lat = lat
        self.lon = lon
        self.speed = speed
        self.vspeed = vspeed
        self.alt = alt
        self.height = height
        self.pilot_lat = pilot_lat
        self.pilot_lon = pilot_lon
        self.description = description
        self.time = time_str  # Refresh the time string on every update

    def to_dict(self) -> dict:
        """Convert the Drone instance to a dictionary for JSON."""
        return {
            'id': self.id,
            'time': self.time,   # So tar1090 can do new Date(drone.time)
            'lat': self.lat,
            'lon': self.lon,
            'speed': self.speed,
            'vspeed': self.vspeed,
            'alt': self.alt,
            'height': self.height,
            'pilot_lat': self.pilot_lat,
            'pilot_lon': self.pilot_lon,
            'description': self.description
        }

def JSONWriter(file, data):
    """Writes data as JSON to a file."""
    try:
        with open(file, 'w', encoding='utf-8') as json_file:
            json.dump(data, json_file, indent=4)
    except (IOError, TypeError) as e:
        logger.error(f"An error occurred while writing to the file: {e}")

def parse_float(value: str) -> float:
    """Parses a string to a float, ignoring any extraneous characters."""
    try:
        return float(value.split()[0])
    except (ValueError, AttributeError):
        return 0.0

class DroneManager:
    """Manages a collection of drones and handles their updates."""
    def __init__(self, max_drones=30):
        # We'll keep a FIFO queue of drone IDs, plus a dict of ID->Drone
        self.drones = deque(maxlen=max_drones)
        self.drone_dict = {}

    def update_or_add_drone(self, drone_id, drone_data: Drone):
        """Updates an existing drone or adds a new one to the collection."""
        if drone_id not in self.drone_dict:
            # If we’re at capacity, remove the oldest
            if len(self.drones) >= self.drones.maxlen:
                oldest = self.drones.popleft()
                del self.drone_dict[oldest]
            self.drones.append(drone_id)
            self.drone_dict[drone_id] = drone_data
        else:
            self.drone_dict[drone_id].update(
                lat=drone_data.lat,
                lon=drone_data.lon,
                speed=drone_data.speed,
                vspeed=drone_data.vspeed,
                alt=drone_data.alt,
                height=drone_data.height,
                pilot_lat=drone_data.pilot_lat,
                pilot_lon=drone_data.pilot_lon,
                description=drone_data.description,
                time_str=drone_data.time
            )

    def remove_old_drones(self, max_age=10):
        """
        If you *still* want to remove drones after 10 seconds of no updates,
        you'll need a numeric timestamp too. Right now we're storing
        ISO strings, so let's just parse them below.
        """
        now = time.time()
        removed = []
        for drone_id in list(self.drones):
            time_str = self.drone_dict[drone_id].time  # e.g. "2024-07-28T20:13:15.123Z"
            try:
                dt = datetime.datetime.fromisoformat(time_str.replace('Z', '+00:00'))
                last_seen = dt.timestamp()
            except ValueError:
                # If the time_str is not parseable, remove it anyway.
                last_seen = 0

            if (now - last_seen) > max_age:
                logger.debug(f"Removing drone: {drone_id} (last seen {time_str})")
                removed.append(drone_id)

        for drone_id in removed:
            self.drones.remove(drone_id)
            del self.drone_dict[drone_id]

    def send_updates(self, file):
        """Write out the entire current drone list to JSON (array-based)."""
        data_to_write = []
        for drone_id in self.drones:
            data_to_write.append(self.drone_dict[drone_id].to_dict())

        JSONWriter(file, data_to_write)

def zmq_to_json(zmqsetting, file, max_drones=30):
    """Main loop: read from ZMQ, parse data, update drones, write JSON."""
    context = zmq.Context()
    zmq_socket = context.socket(zmq.SUB)
    zmq_socket.connect(f"tcp://{zmqsetting}")
    zmq_socket.setsockopt_string(zmq.SUBSCRIBE, "")

    drone_manager = DroneManager(max_drones=max_drones)
    
    def signal_handler(sig, frame):
        print("Interrupted by user")
        zmq_socket.close()
        context.term()
        print("Cleaned up ZMQ resources")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    try:
        while True:
            try:
                message = zmq_socket.recv_json()
                drone_info = {}

                for item in message:
                    # Basic ID
                    if 'Basic ID' in item:
                        id_type = item['Basic ID'].get('id_type')
                        if id_type == 'Serial Number (ANSI/CTA-2063-A)' and 'id' not in drone_info:
                            drone_info['id'] = item['Basic ID'].get('id', 'unknown')
                        elif id_type == 'CAA Assigned Registration ID' and 'id' not in drone_info:
                            drone_info['id'] = item['Basic ID'].get('id', 'unknown')

                    if 'id' in drone_info:
                        # ensure it starts with drone-
                        if not drone_info['id'].startswith('drone-'):
                            drone_info['id'] = f"drone-{drone_info['id']}"

                        # Always set 'time' to an ISO8601 string
                        drone_info['time'] = iso_timestamp_now()

                    # Location/Vector
                    if 'Location/Vector Message' in item:
                        drone_info['lat'] = parse_float(item['Location/Vector Message'].get('latitude', "0.0"))
                        drone_info['lon'] = parse_float(item['Location/Vector Message'].get('longitude', "0.0"))
                        drone_info['speed'] = parse_float(item['Location/Vector Message'].get('speed', "0.0"))
                        drone_info['vspeed'] = parse_float(item['Location/Vector Message'].get('vert_speed', "0.0"))
                        drone_info['alt'] = parse_float(item['Location/Vector Message'].get('geodetic_altitude', "0.0"))
                        drone_info['height'] = parse_float(item['Location/Vector Message'].get('height_agl', "0.0"))

                    # Self-ID
                    if 'Self-ID Message' in item:
                        drone_info['description'] = item['Self-ID Message'].get('text', "")

                    # System
                    if 'System Message' in item:
                        drone_info['pilot_lat'] = parse_float(item['System Message'].get('latitude', "0.0"))
                        drone_info['pilot_lon'] = parse_float(item['System Message'].get('longitude', "0.0"))

                # If there's an ID, create/update the Drone object
                if 'id' in drone_info:
                    drone_id = drone_info['id']
                    new_drone = Drone(
                        id=drone_info['id'],
                        lat=drone_info.get('lat', 0.0),
                        lon=drone_info.get('lon', 0.0),
                        speed=drone_info.get('speed', 0.0),
                        vspeed=drone_info.get('vspeed', 0.0),
                        alt=drone_info.get('alt', 0.0),
                        height=drone_info.get('height', 0.0),
                        pilot_lat=drone_info.get('pilot_lat', 0.0),
                        pilot_lon=drone_info.get('pilot_lon', 0.0),
                        description=drone_info.get('description', ""),
                        # fallback to right now if not set
                        time_str=drone_info.get('time', iso_timestamp_now())
                    )
                    drone_manager.update_or_add_drone(drone_id, new_drone)

                # After updating, write out JSON
                drone_manager.send_updates(file)

            except Exception as e:
                logger.error(f"Error receiving or processing message: {e}")

            # Periodically remove drones older than 10s if desired
            drone_manager.remove_old_drones(max_age=10)

    except KeyboardInterrupt:
        signal_handler(None, None)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ZMQ to JSON with 'time' for Tar1090.")
    parser.add_argument("--zmqsetting", default="127.0.0.1:4224", help="ZMQ server to connect to")
    parser.add_argument("--json-file", default="/run/readsb/drone.json", help="JSON file to write parsed data to")
    parser.add_argument("--max-drones", type=int, default=30, help="Maximum number of drones to track")
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    logging.info("Starting ZMQ to JSON with log level: %s", "DEBUG" if args.debug else "INFO")

    zmq_to_json(args.zmqsetting, args.json_file, max_drones=args.max_drones)
