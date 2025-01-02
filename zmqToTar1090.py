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
import re

# Configure logging
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

def JSONWriter(file, data: list):
    """Writes drone data to a JSON file."""
    try:
        with open(file, 'w', encoding='utf-8') as json_file:
            json.dump(data, json_file, indent=4)
    except (IOError, TypeError) as e:
        logger.error(f"An error occurred while writing to the file: {e}")

def is_valid_latlon(lat: float, lon: float) -> bool:
    """Check if latitude and longitude are within valid ranges."""
    if (lat < -90.0 or lat > 90.0) or (lat == 0.0):
        return False
    if (lon < -180.0 or lon > 180.0) or (lon == 0.0):
        return False
    return True

def is_valid_mac(mac: str) -> bool:
    """Validate MAC address format."""
    if not mac:
        return False
    mac_regex = re.compile(r'^([0-9A-Fa-f]{2}:){5}([0-9A-Fa-f]{2})$')
    return bool(mac_regex.match(mac))

class Drone:
    """Represents a drone and its telemetry data."""
    def __init__(self, id: str, mac: str = ""):
        self.id = id  # Serial Number or Pilot ID
        self.mac = mac.lower() if mac else ""  # Ensure MAC is lowercase if provided
        # Telemetry Data
        self.lat = 0.0
        self.lon = 0.0
        self.speed = 0.0
        self.vspeed = 0.0
        self.alt = 0.0
        self.height = 0.0
        self.pilot_lat = 0.0
        self.pilot_lon = 0.0
        self.description = ""
        self.time = iso_timestamp_now()  # Last updated time

    def update(self, data: dict):
        """Updates the drone's telemetry data and last seen time."""
        # Update fields if present in the incoming data
        self.lat = data.get('lat', self.lat)
        self.lon = data.get('lon', self.lon)
        self.speed = data.get('speed', self.speed)
        self.vspeed = data.get('vspeed', self.vspeed)
        self.alt = data.get('alt', self.alt)
        self.height = data.get('height', self.height)
        self.pilot_lat = data.get('pilot_lat', self.pilot_lat)
        self.pilot_lon = data.get('pilot_lon', self.pilot_lon)
        self.description = data.get('description', self.description)
        self.time = data.get('time', iso_timestamp_now())

    def to_dict(self) -> dict:
        """Convert the Drone instance to a dictionary for JSON serialization."""
        drone_dict = {
            "id": self.id,
            "time": self.time,
            "lat": self.lat,
            "lon": self.lon,
            "speed": self.speed,
            "vspeed": self.vspeed,
            "alt": self.alt,
            "height": self.height,
            "description": self.description
        }
        # Include pilot information only if both pilot_lat and pilot_lon are non-zero
        if self.pilot_lat != 0.0 and self.pilot_lon != 0.0:
            drone_dict["pilot_lat"] = self.pilot_lat
            drone_dict["pilot_lon"] = self.pilot_lon
        return drone_dict

class DroneManager:
    """Manages a collection of drones and handles their updates based on MAC address."""
    def __init__(self, max_drones=30):
        self.drones = deque(maxlen=max_drones)          # To maintain insertion order and limit size
        self.mac_to_drone_id = {}                       # Maps MAC addresses to main drone IDs
        self.drone_dict = {}                             # Maps drone IDs (main and pilot) to Drone instances

    def update_or_add_main_drone(self, drone_info: dict) -> str:
        """
        Updates an existing main drone or adds a new one based on MAC address.
        `drone_info` must include 'id' and 'mac'.
        Returns the main drone ID if successful, else None.
        """
        mac = drone_info.get('mac')
        drone_id = drone_info.get('id')

        if not mac:
            logger.debug(f"Drone '{drone_id}' has no MAC address. Skipping...")
            return None

        if mac in self.mac_to_drone_id:
            # Existing drone, update based on MAC
            existing_id = self.mac_to_drone_id[mac]
            drone = self.drone_dict[existing_id]
            if drone_id != drone.id:
                logger.debug(f"Conflicting IDs for MAC '{mac}': '{drone.id}' vs '{drone_id}'. Using original ID.")
                # Decide whether to overwrite ID or retain original. Currently retaining original.
            drone.update(drone_info)
            logger.debug(f"Updated drone '{drone.id}' with MAC '{mac}'.")
            return existing_id
        else:
            # New drone, add to manager
            if len(self.drones) >= self.drones.maxlen:
                oldest_mac, oldest_id = self.drones.popleft()
                del self.mac_to_drone_id[oldest_mac]
                del self.drone_dict[oldest_id]
                logger.debug(f"Removed oldest drone '{oldest_id}' with MAC '{oldest_mac}'.")
            self.drones.append((mac, drone_id))
            new_drone = Drone(id=drone_id, mac=mac)
            new_drone.update(drone_info)
            self.drone_dict[drone_id] = new_drone
            self.mac_to_drone_id[mac] = drone_id
            logger.debug(f"Added new drone '{drone_id}' with MAC '{mac}'.")
            return drone_id

    def update_or_add_pilot_drone(self, main_drone_id: str, drone_info: dict):
        """
        Creates or updates a pilot drone associated with the main drone.
        Pilot drone ID is 'pilot-' + main drone ID.
        Pilot drones do not have MAC addresses.
        """
        pilot_id = f"pilot-{main_drone_id}"
        pilot_lat = drone_info.get('pilot_lat', 0.0)
        pilot_lon = drone_info.get('pilot_lon', 0.0)

        if is_valid_latlon(pilot_lat, pilot_lon):
            if pilot_id not in self.drone_dict:
                # Add new pilot drone
                new_pilot = Drone(id=pilot_id)
                new_pilot.lat = pilot_lat
                new_pilot.lon = pilot_lon
                new_pilot.speed = 0.0
                new_pilot.vspeed = 0.0
                new_pilot.alt = 0.0
                new_pilot.height = 0.0
                new_pilot.pilot_lat = 0.0
                new_pilot.pilot_lon = 0.0
                new_pilot.description = self.drone_dict[main_drone_id].description
                new_pilot.time = drone_info.get('time', iso_timestamp_now())
                self.drones.append((None, pilot_id))  # Pilots don't have a MAC
                self.drone_dict[pilot_id] = new_pilot
                logger.debug(f"Added new pilot drone '{pilot_id}'.")
            else:
                # Update existing pilot drone
                pilot_drone = self.drone_dict[pilot_id]
                pilot_drone.lat = pilot_lat
                pilot_drone.lon = pilot_lon
                pilot_drone.time = drone_info.get('time', iso_timestamp_now())
                logger.debug(f"Updated pilot drone '{pilot_id}'.")
        else:
            # Pilot coordinates are invalid; remove pilot drone if exists
            if pilot_id in self.drone_dict:
                logger.debug(f"Removing stale pilot drone '{pilot_id}' due to invalid or zero coordinates.")
                self.drones = deque([(m, d) for (m, d) in self.drones if d != pilot_id], maxlen=self.drones.maxlen)
                del self.drone_dict[pilot_id]

    def remove_old_drones(self, max_age: float):
        """Removes drones that haven't been updated in > max_age seconds."""
        now_ts = time.time()
        remove_list = []

        for drone_id, drone in list(self.drone_dict.items()):
            if drone.id.startswith("pilot-"):
                # Pilot drones are handled with their main drones
                continue
            iso_str = drone.time
            try:
                dt = datetime.datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
                last_seen_ts = dt.timestamp()
            except ValueError:
                # If 'time' is invalid, remove it to be safe
                logger.debug(f"Removing {drone_id}, invalid time field: {iso_str}")
                remove_list.append(drone_id)
                continue

            if (now_ts - last_seen_ts) > max_age:
                remove_list.append(drone_id)

        for drone_id in remove_list:
            drone = self.drone_dict[drone_id]
            mac = None
            # Find the MAC associated with this drone
            for m, d_id in self.mac_to_drone_id.items():
                if d_id == drone_id:
                    mac = m
                    break
            if mac:
                self.drones = deque([(m, d) for (m, d) in self.drones if m != mac], maxlen=self.drones.maxlen)
                del self.mac_to_drone_id[mac]
            del self.drone_dict[drone_id]
            logger.debug(f"Removed stale drone '{drone_id}' with MAC '{mac}'.")
            # Also remove associated pilot drone if exists
            pilot_id = f"pilot-{drone_id}"
            if pilot_id in self.drone_dict:
                logger.debug(f"Removed stale pilot drone '{pilot_id}' associated with '{drone_id}'.")
                self.drones = deque([(m, d) for (m, d) in self.drones if d != pilot_id], maxlen=self.drones.maxlen)
                del self.drone_dict[pilot_id]

    def to_json_list(self) -> list:
        """Converts all drones to a list of dictionaries for JSON serialization."""
        return [drone.to_dict() for drone in self.drone_dict.values()]

    def send_updates(self, file: str):
        """Writes the current drone data to the specified JSON file."""
        data_to_write = self.to_json_list()
        try:
            JSONWriter(file, data_to_write)
            logger.debug(f"Updated JSON file '{file}' with {len(data_to_write)} drones.")
        except Exception as e:
            logger.error(f"Error writing JSON: {e}")

def parse_esp32_dict(message: dict) -> dict:
    """Parses ESP32 formatted drone data (single dict) and extracts relevant information including MAC address."""
    drone_info = {}

    # Check for 'Basic ID'
    if 'Basic ID' in message:
        id_type = message['Basic ID'].get('id_type')
        if id_type == 'Serial Number (ANSI/CTA-2063-A)' and 'id' not in drone_info:
            drone_info['id'] = message['Basic ID'].get('id', 'unknown')
        elif id_type == 'CAA Assigned Registration ID' and 'id' not in drone_info:
            drone_info['id'] = message['Basic ID'].get('id', 'unknown')

        # Extract MAC address
        mac = message['Basic ID'].get('MAC')
        if mac and is_valid_mac(mac):
            drone_info['mac'] = mac.lower()  # Standardize to lowercase
        else:
            logger.debug(f"Invalid or missing MAC address in ESP32 message: '{mac}'.")

    # Custom 'drone_id' key (if exists)
    if 'drone_id' in message and 'id' not in drone_info:
        drone_info['id'] = message['drone_id']

    # Parse location data
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

    # Pilot lat/lon
    if 'pilot_lat' in message:
        drone_info['pilot_lat'] = parse_float(str(message['pilot_lat']))
    if 'pilot_lon' in message:
        drone_info['pilot_lon'] = parse_float(str(message['pilot_lon']))

    # Top-level 'description'
    if 'description' in message:
        drone_info['description'] = message['description']
    else:
        drone_info['description'] = message.get('Self-ID Message', {}).get('text', "")

    return drone_info

def parse_list_format(message_list: list) -> dict:
    """Parses Bluetooth formatted drone data (list of dicts) and extracts relevant information including MAC address."""
    drone_info = {}
    drone_info['mac'] = None

    for item in message_list:
        if not isinstance(item, dict):
            logger.debug(f"Unexpected item in list: {item}")
            continue

        # Basic ID
        if 'Basic ID' in item:
            id_type = item['Basic ID'].get('id_type')
            if id_type == 'Serial Number (ANSI/CTA-2063-A)' and 'id' not in drone_info:
                drone_info['id'] = item['Basic ID'].get('id', 'unknown')
            elif id_type == 'CAA Assigned Registration ID' and 'id' not in drone_info:
                drone_info['id'] = item['Basic ID'].get('id', 'unknown')

            # Extract MAC address
            mac = item['Basic ID'].get('MAC')
            if mac and is_valid_mac(mac):
                drone_info['mac'] = mac.lower()  # Standardize to lowercase
            else:
                logger.debug(f"Invalid or missing MAC address in Bluetooth message: '{mac}'.")

        # Location/Vector
        if 'Location/Vector Message' in item:
            loc_vec = item['Location/Vector Message']
            drone_info['lat'] = parse_float(str(loc_vec.get('latitude', "0.0")))
            drone_info['lon'] = parse_float(str(loc_vec.get('longitude', "0.0")))
            drone_info['speed'] = parse_float(str(loc_vec.get('speed', "0.0")))
            drone_info['vspeed'] = parse_float(str(loc_vec.get('vert_speed', "0.0")))
            drone_info['alt'] = parse_float(str(loc_vec.get('geodetic_altitude', "0.0")))
            drone_info['height'] = parse_float(str(loc_vec.get('height_agl', "0.0")))

        # Self-ID
        if 'Self-ID Message' in item:
            drone_info['description'] = item['Self-ID Message'].get('text', "")

        # System
        if 'System Message' in item:
            sysm = item['System Message']
            drone_info['pilot_lat'] = parse_float(sysm.get('latitude', "0.0"))
            drone_info['pilot_lon'] = parse_float(sysm.get('longitude', "0.0"))

    return drone_info

def zmq_to_json(zmqsetting: str, file: str, max_age: float, max_drones: int = 30):
    """Processes ZMQ data and writes consolidated drone information to a JSON file.
    Ensures one entry per real drone based on MAC address, consolidating multiple bursts.
    Pilot information is included only if both pilot_lat and pilot_lon are non-zero.
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
        logger.info("Cleaned up ZMQ resources.")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    logger.info("Starting ZMQ listener. Waiting for messages...")

    while True:
        try:
            message = zmq_socket.recv_json()
        except Exception as e:
            logger.error(f"Error receiving JSON from ZMQ: {e}")
            continue

        # Determine the message format and parse accordingly
        try:
            if isinstance(message, list):
                # Bluetooth message (list of dicts)
                drone_info = parse_list_format(message)
            elif isinstance(message, dict):
                # ESP32 message (single dict)
                drone_info = parse_esp32_dict(message)
            else:
                logger.debug("Unknown ZMQ payload type - not list (Bluetooth) or dict (ESP32). Skipping.")
                continue
        except Exception as e:
            logger.error(f"Error parsing incoming message: {e}")
            continue

        # Ensure both 'id' and 'mac' are present
        if 'id' in drone_info and 'mac' in drone_info and drone_info['mac']:
            # Prefix with 'drone-' if not already
            if not drone_info['id'].startswith('drone-'):
                drone_info['id'] = f"drone-{drone_info['id']}"

            # Add a 'time' field in ISO8601 for tar1090 ingestion
            drone_info['time'] = iso_timestamp_now()

            # Update or add the main drone based on MAC address
            main_drone_id = drone_manager.update_or_add_main_drone(drone_info)

            if main_drone_id:
                # Grab the main drone's coordinates
                main_lat = drone_info.get('lat', 0.0)
                main_lon = drone_info.get('lon', 0.0)

                # Grab the pilot's coordinates
                pilot_lat = drone_info.get('pilot_lat', 0.0)
                pilot_lon = drone_info.get('pilot_lon', 0.0)

                # If main drone lat/lon is invalid, skip adding the drone and remove any existing pilot
                if not is_valid_latlon(main_lat, main_lon):
                    logger.debug(f"Skipping drone {drone_info['id']} - invalid lat/lon: ({main_lat}, {main_lon})")
                    # Remove any existing pilot entry
                    drone_manager.update_or_add_pilot_drone(main_drone_id, {'pilot_lat': 0.0, 'pilot_lon': 0.0, 'time': drone_info['time']})
                    continue

                # Update or add the pilot drone based on pilot coordinates
                drone_manager.update_or_add_pilot_drone(main_drone_id, drone_info)

            else:
                # If main_drone_id is None, skip pilot handling
                continue

        else:
            logger.debug("No valid 'id' or 'mac' found in message. Skipping...")

        # After updating, write JSON
        drone_manager.send_updates(file)

        # Remove any drones that haven't been updated for > max_age seconds
        drone_manager.remove_old_drones(max_age)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ZMQ to JSON for tar1090, handling standard & ESP32 formats with MAC-based consolidation.")
    parser.add_argument("--zmqsetting", default="127.0.0.1:4224", help="Define ZMQ server to connect to (default=127.0.0.1:4224)")
    parser.add_argument("--json-file", default="/run/readsb/drone.json", help="JSON file to write parsed data to. (default=/run/readsb/drone.json)")
    parser.add_argument("--max-age", default=10, help="Number of seconds before drone is old and removed from JSON file (default=10)", type=float)
    parser.add_argument("--max-drones", default=30, help="Maximum number of drones to track (default=30)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()

    # Configure logging level and format
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    logger.info(f"Starting ZMQ to JSON with log level: {'DEBUG' if args.verbose else 'INFO'}")

    zmq_to_json(args.zmqsetting, args.json_file, args.max_age, args.max_drones)
