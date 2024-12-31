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
    """Parses a string to a float, ignoring any extraneous characters.
       Returns 0.0 if parsing fails."""
    try:
        return float(value.split()[0])
    except (ValueError, AttributeError):
        return 0.0

def is_valid_latlon(lat: float, lon: float) -> bool:
    """Quick check if lat/lon is within plausible bounds."""
    if lat < -90.0 or lat > 90.0:
        return False
    if lon < -180.0 or lon > 180.0:
        return False
    return True

class Drone:
    """Represents a drone (or pilot) for tar1090 data."""
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
        self.time = time_str  # ISO8601 date/time

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
        """Convert the Drone instance to a dictionary Tar1090 can parse."""
        return {
            "id": self.id,
            "time": self.time,
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
    """Manages drones/pilots for tar1090."""
    def __init__(self, max_drones=30):
        self.drones = deque(maxlen=max_drones)  # track IDs in FIFO
        self.drone_dict = {}

    def update_or_add_drone(self, drone_id: str, new_data: Drone):
        """Updates or adds a new drone/pilot entry."""
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
        Removes drones/pilots that haven't been updated in > max_age seconds,
        based on the 'time' field (ISO8601).
        """
        now_ts = time.time()
        remove_list = []

        for drone_id in list(self.drones):
            iso_str = self.drone_dict[drone_id].time
            try:
                dt = datetime.datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
                last_seen_ts = dt.timestamp()
            except ValueError:
                # If 'time' is invalid for some reason, remove it to be safe
                logger.warning(f"Removing {drone_id}, invalid time field: {iso_str}")
                remove_list.append(drone_id)
                continue

            if (now_ts - last_seen_ts) > max_age:
                remove_list.append(drone_id)

        for d_id in remove_list:
            logger.debug(f"Removing old entry {d_id}")
            if d_id in self.drones:
                self.drones.remove(d_id)
            if d_id in self.drone_dict:
                del self.drone_dict[d_id]

    def send_updates(self, json_file):
        """Writes out the entire current drone/pilot list to JSON."""
        data_to_write = []
        for d_id in self.drones:
            data_to_write.append(self.drone_dict[d_id].to_dict())

        try:
            with open(json_file, 'w', encoding='utf-8') as fp:
                json.dump(data_to_write, fp, indent=4)
        except Exception as e:
            logger.error(f"Error writing JSON to {json_file}: {e}")

#
# Example parse functions
#
def parse_list_format(message_list: list) -> dict:
    """Parses the old array-of-dicts format."""
    drone_info = {}
    for item in message_list:
        if not isinstance(item, dict):
            logger.error(f"Unexpected item in list: {item} (expected dict).")
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
            lvm = item['Location/Vector Message']
            drone_info['lat'] = parse_float(lvm.get('latitude', "0.0"))
            drone_info['lon'] = parse_float(lvm.get('longitude', "0.0"))
            drone_info['speed'] = parse_float(lvm.get('speed', "0.0"))
            drone_info['vspeed'] = parse_float(lvm.get('vert_speed', "0.0"))
            drone_info['alt'] = parse_float(lvm.get('geodetic_altitude', "0.0"))
            drone_info['height'] = parse_float(lvm.get('height_agl', "0.0"))

        # Self-ID
        if 'Self-ID Message' in item:
            drone_info['description'] = item['Self-ID Message'].get('text', "")

        # System
        if 'System Message' in item:
            sysm = item['System Message']
            drone_info['pilot_lat'] = parse_float(sysm.get('latitude', "0.0"))
            drone_info['pilot_lon'] = parse_float(sysm.get('longitude', "0.0"))

    return drone_info

def parse_esp32_dict(message: dict) -> dict:
    """Parses a single-dict style from ESP32."""
    drone_info = {}

    # Check for 'Basic ID'
    if 'Basic ID' in message:
        id_type = message['Basic ID'].get('id_type')
        if id_type == 'Serial Number (ANSI/CTA-2063-A)' and 'id' not in drone_info:
            drone_info['id'] = message['Basic ID'].get('id', 'unknown')
        elif id_type == 'CAA Assigned Registration ID' and 'id' not in drone_info:
            drone_info['id'] = message['Basic ID'].get('id', 'unknown')

    # Possibly a custom 'drone_id'
    if 'drone_id' in message and 'id' not in drone_info:
        drone_info['id'] = message['drone_id']

    # Parse location data
    # We wrap parse_float() calls in str() to handle ints or floats
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

    if 'pilot_lat' in message:
        drone_info['pilot_lat'] = parse_float(str(message['pilot_lat']))
    if 'pilot_lon' in message:
        drone_info['pilot_lon'] = parse_float(str(message['pilot_lon']))

    if 'description' in message:
        drone_info['description'] = message['description']
    else:
        drone_info['description'] = message.get('Self-ID Message', {}).get('text', "")

    return drone_info


def zmq_to_json(zmqsetting, file, max_drones=30):
    """
    Main tar1090-ish script. If pilot lat/lon is present, 
    we add a second marker with pilot-xxx. Both share the same timestamp.
    """
    context = zmq.Context()
    zmq_socket = context.socket(zmq.SUB)
    zmq_socket.connect(f"tcp://{zmqsetting}")
    zmq_socket.setsockopt_string(zmq.SUBSCRIBE, "")

    drone_manager = DroneManager(max_drones=max_drones)
    
    def signal_handler(sig, frame):
        logger.info("Interrupted by user. Shutting down.")
        zmq_socket.close()
        context.term()
        logger.info("Cleaned up ZMQ resources")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    while True:
        try:
            message = zmq_socket.recv_json()
        except Exception as e:
            logger.error(f"Error receiving JSON from ZMQ: {e}")
            continue

        # Decide which parser to use
        try:
            if isinstance(message, list):
                drone_info = parse_list_format(message)
            elif isinstance(message, dict):
                drone_info = parse_esp32_dict(message)
            else:
                logger.error("Unknown ZMQ payload type (not list or dict). Skipping.")
                continue
        except Exception as e:
            logger.error(f"Error parsing incoming message: {e}")
            continue

        # If we have an ID, prefix with 'drone-' if needed
        if 'id' in drone_info:
            if not drone_info['id'].startswith('drone-'):
                drone_info['id'] = f"drone-{drone_info['id']}"

            # Grab the pilot coords
            pilot_lat = drone_info.get('pilot_lat', 0.0)
            pilot_lon = drone_info.get('pilot_lon', 0.0)

            # Always add a 'time' field in ISO8601 format
            iso_time = iso_timestamp_now()

            # Validate the main drone lat/lon
            main_lat = drone_info.get('lat', 0.0)
            main_lon = drone_info.get('lon', 0.0)

            # If lat/lon is invalid, skip adding the drone
            if not is_valid_latlon(main_lat, main_lon):
                logger.warning(f"Skipping drone {drone_info['id']} - invalid lat/lon: ({main_lat}, {main_lon})")
                # Possibly also remove a leftover pilot if it exists
                pilot_id = drone_info['id'].replace("drone-", "pilot-")
                if pilot_id in drone_manager.drone_dict:
                    logger.debug(f"Removing stale pilot entry for invalid drone: {pilot_id}")
                    if pilot_id in drone_manager.drones:
                        drone_manager.drones.remove(pilot_id)
                    del drone_manager.drone_dict[pilot_id]
                continue

            # 1) Create/Update the main drone
            main_drone = Drone(
                id=drone_info['id'],
                lat=main_lat,
                lon=main_lon,
                speed=drone_info.get('speed', 0.0),
                vspeed=drone_info.get('vspeed', 0.0),
                alt=drone_info.get('alt', 0.0),
                height=drone_info.get('height', 0.0),
                pilot_lat=pilot_lat,
                pilot_lon=pilot_lon,
                description=drone_info.get('description', ""),
                time_str=iso_time
            )
            drone_manager.update_or_add_drone(main_drone.id, main_drone)

            # 2) If pilot lat/lon is valid, create second "pilot" object
            if is_valid_latlon(pilot_lat, pilot_lon):
                pilot_id = main_drone.id.replace("drone-", "pilot-")
                pilot_drone = Drone(
                    id=pilot_id,
                    lat=pilot_lat,
                    lon=pilot_lon,
                    speed=0.0,
                    vspeed=0.0,
                    alt=0.0,
                    height=0.0,
                    pilot_lat=0.0,
                    pilot_lon=0.0,
                    description=main_drone.description,
                    time_str=iso_time
                )
                drone_manager.update_or_add_drone(pilot_id, pilot_drone)
            else:
                # If pilot lat/lon is invalid or zero, remove leftover pilot
                pilot_id = main_drone.id.replace("drone-", "pilot-")
                if pilot_id in drone_manager.drone_dict:
                    logger.debug(f"Removing stale pilot entry {pilot_id} (invalid or no pilot coords)")
                    if pilot_id in drone_manager.drones:
                        drone_manager.drones.remove(pilot_id)
                    del drone_manager.drone_dict[pilot_id]
        else:
            logger.warning("No 'id' found in the message. Skipping...")

        # After updating, write JSON
        drone_manager.send_updates(file)

        # Remove any drones/pilots that haven't been updated in >10s
        drone_manager.remove_old_drones(max_age=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ZMQ to JSON for tar1090, with pilot-loc logic and error checks.")
    parser.add_argument("--zmqsetting", default="127.0.0.1:4224", help="ZMQ server to connect to (host:port)")
    parser.add_argument("--json-file", default="/run/readsb/drone.json", help="Where to write the JSON")
    parser.add_argument("--max-drones", type=int, default=30, help="Max number of drones/pilots to track")
    parser.add_argument("-d", "--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    logging.info("Starting ZMQ to JSON with pilot logic and extra error checks.")
    logging.info("Log level: %s", "DEBUG" if args.debug else "INFO")

    zmq_to_json(args.zmqsetting, args.json_file, max_drones=args.max_drones)
