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
    """Return current time as an ISO8601 string with 'Z' for UTC."""
    return datetime.datetime.utcnow().isoformat(timespec='milliseconds') + 'Z'

def parse_float(value: str) -> float:
    """Parses a string to a float, ignoring any extraneous characters."""
    try:
        return float(value.split()[0])
    except (ValueError, AttributeError):
        return 0.0

class Drone:
    """Represents a drone and its telemetry data."""
    def __init__(self, 
                 id: str,
                 lat: float,
                 lon: float,
                 speed: float,
                 vspeed: float,
                 alt: float,
                 height: float,
                 pilot_lat: float,
                 pilot_lon: float,
                 description: str,
                 time_str: str):
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
        # For tar1090: an ISO8601 date/time string
        self.time = time_str  # "2024-01-02T03:04:05.678Z"

    def update(self,
               lat: float,
               lon: float,
               speed: float,
               vspeed: float,
               alt: float,
               height: float,
               pilot_lat: float,
               pilot_lon: float,
               description: str,
               time_str: str):
        """Updates the drone's telemetry data and time."""
        self.lat = lat
        self.lon = lon
        self.speed = speed
        self.vspeed = vspeed
        self.alt = alt
        self.height = height
        self.pilot_lat = pilot_lat
        self.pilot_lon = pilot_lon
        self.description = description
        self.time = time_str

    def to_dict(self) -> dict:
        """Convert the Drone instance to a dict that tar1090 can use."""
        return {
            "id": self.id,       # you can keep "id" or rename to "hex"
            "time": self.time,   # iso8601, tar1090 can parse this
            "lat": self.lat,
            "lon": self.lon,
            "speed": self.speed,
            "vspeed": self.vspeed,
            "alt": self.alt,
            "height": self.height,
            "pilot_lat": self.pilot_lat,
            "pilot_lon": self.pilot_lon,
            "description": self.description
        }

class DroneManager:
    """Manages a collection of drones and handles their updates."""
    def __init__(self, max_drones=30):
        self.drones = deque(maxlen=max_drones)  # track drone IDs in FIFO
        self.drone_dict = {}

    def update_or_add_drone(self, drone_id: str, new_data: Drone):
        """Updates or adds a new drone to the manager."""
        if drone_id not in self.drone_dict:
            # If at capacity, remove oldest
            if len(self.drones) >= self.drones.maxlen:
                oldest_id = self.drones.popleft()
                del self.drone_dict[oldest_id]
            self.drones.append(drone_id)
            self.drone_dict[drone_id] = new_data
        else:
            # Update existing
            self.drone_dict[drone_id].update(
                lat=new_data.lat,
                lon=new_data.lon,
                speed=new_data.speed,
                vspeed=new_data.vspeed,
                alt=new_data.alt,
                height=new_data.height,
                pilot_lat=new_data.pilot_lat,
                pilot_lon=new_data.pilot_lon,
                description=new_data.description,
                time_str=new_data.time
            )

    def remove_old_drones(self, max_age=10):
        """
        Removes drones that haven't been updated in > max_age seconds.
        We'll parse the 'time' field (ISO8601) to compare against now.
        """
        now_ts = time.time()
        remove_list = []

        for drone_id in list(self.drones):
            iso_str = self.drone_dict[drone_id].time
            try:
                dt = datetime.datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
                last_seen_ts = dt.timestamp()
            except ValueError:
                # If something's wrong with the date format, remove it
                last_seen_ts = 0.0

            if (now_ts - last_seen_ts) > max_age:
                remove_list.append(drone_id)

        for d_id in remove_list:
            logger.debug(f"Removing old drone {d_id}")
            self.drones.remove(d_id)
            del self.drone_dict[d_id]

    def send_updates(self, json_file):
        """
        Writes the data in array-of-objects format. Each object has a 'time' field
        so tar1090 can parse how recent it is.
        """
        data_to_write = []
        for drone_id in self.drones:
            data_to_write.append(self.drone_dict[drone_id].to_dict())

        try:
            with open(json_file, 'w', encoding='utf-8') as fp:
                json.dump(data_to_write, fp, indent=4)
        except Exception as e:
            logger.error(f"Error writing JSON: {e}")

def parse_esp32_dict(message: dict) -> dict:
    """
    Example parser for an ESP32 style message. 
    Suppose it has top-level fields like 'drone_id', 'latitude', 'longitude', etc.
    Adjust as needed.
    """
    drone_info = {}

    # Basic ID might still exist, or it might not
    if 'Basic ID' in message:
        id_type = message['Basic ID'].get('id_type')
        if id_type == 'Serial Number (ANSI/CTA-2063-A)' and 'id' not in drone_info:
            drone_info['id'] = message['Basic ID'].get('id', 'unknown')
        elif id_type == 'CAA Assigned Registration ID' and 'id' not in drone_info:
            drone_info['id'] = message['Basic ID'].get('id', 'unknown')

    # If the ESP32 has a custom 'drone_id' key
    if 'drone_id' in message and 'id' not in drone_info:
        drone_info['id'] = message['drone_id']

    # Grab lat/lon, or fall back to "Location/Vector Message" if that also exists
    if 'latitude' in message:
        drone_info['lat'] = parse_float(str(message['latitude']))
    if 'longitude' in message:
        drone_info['lon'] = parse_float(str(message['longitude']))
    if 'altitude' in message:
        drone_info['alt'] = parse_float(str(message['altitude']))
    if 'speed' in message:
        drone_info['speed'] = parse_float(str(message['speed']))
    if 'vert_speed' in message:
        drone_info['vspeed'] = parse_float(str(message['vert_speed']))
    if 'height' in message:
        drone_info['height'] = parse_float(str(message['height']))

    # Possibly pilot lat/lon
    if 'pilot_lat' in message:
        drone_info['pilot_lat'] = parse_float(str(message['pilot_lat']))
    if 'pilot_lon' in message:
        drone_info['pilot_lon'] = parse_float(str(message['pilot_lon']))

    # Maybe a top-level 'description'
    if 'description' in message:
        drone_info['description'] = message['description']
    else:
        drone_info['description'] = message.get('Self-ID Message', {}).get('text', "")

    return drone_info

def parse_list_format(message_list: list) -> dict:
    """
    The old format: an array of dicts containing 
    'Basic ID', 'Location/Vector Message', 'Self-ID Message', etc.
    """
    drone_info = {}
    for item in message_list:
        if not isinstance(item, dict):
            logger.error(f"Unexpected item in list: {item}")
            continue

        # Basic ID
        if 'Basic ID' in item:
            id_type = item['Basic ID'].get('id_type')
            if id_type == 'Serial Number (ANSI/CTA-2063-A)' and 'id' not in drone_info:
                drone_info['id'] = item['Basic ID'].get('id', 'unknown')
            elif id_type == 'CAA Assigned Registration ID' and 'id' not in drone_info:
                drone_info['id'] = item['Basic ID'].get('id', 'unknown')

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

    return drone_info

def zmq_to_json(zmqsetting, file, max_drones=30):
    """
    Script that connects to ZMQ, receives messages, 
    handles either old list-based or new ESP32 dict-based format,
    and writes data for tar1090.
    """
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

    while True:
        try:
            message = zmq_socket.recv_json()
            # Decide which parser to use
            if isinstance(message, list):
                drone_info = parse_list_format(message)
            elif isinstance(message, dict):
                drone_info = parse_esp32_dict(message)
            else:
                logger.error("Unknown ZMQ payload type (not list or dict). Skipping.")
                continue

            # If we have an ID, prefix with 'drone-' if needed
            if 'id' in drone_info:
                if not drone_info['id'].startswith('drone-'):
                    drone_info['id'] = f"drone-{drone_info['id']}"

                # Always add a 'time' field in ISO8601 for tar1090
                drone_time = iso_timestamp_now()

                # Create or update a Drone object
                drone = Drone(
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
                    time_str=drone_time
                )

                drone_manager.update_or_add_drone(drone_info['id'], drone)
            else:
                logger.warning("No 'id' found in message. Skipping...")

            # After updating, write JSON
            drone_manager.send_updates(file)

            # Remove any drones that haven't been updated for > 10s
            drone_manager.remove_old_drones(max_age=10)

        except Exception as e:
            logger.error(f"Error receiving or processing message: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ZMQ to JSON for tar1090, handling old & ESP32 formats.")
    parser.add_argument("--zmqsetting", default="127.0.0.1:4224", help="ZMQ server to connect to (host:port)")
    parser.add_argument("--json-file", default="/run/readsb/drone.json", help="JSON file to write tar1090 data to")
    parser.add_argument("--max-drones", type=int, default=30, help="Maximum number of drones to track")
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    logging.info("Starting ZMQ to JSON with log level: %s", "DEBUG" if args.debug else "INFO")

    zmq_to_json(args.zmqsetting, args.json_file, max_drones=args.max_drones)
