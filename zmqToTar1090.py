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
import shutil
import os  # <-- Already imported, ensuring we can check file existence.

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

def JSONWriter(file, data: list, create_backup: bool = False):
    """Writes drone data to a JSON file with optional backup."""
    try:
        if create_backup:
            # Create a backup with timestamp
            backup_file = f"{file}.{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}.bak"
            shutil.copyfile(file, backup_file)
            logger.debug(f"Created backup of JSON file at '{backup_file}'.")
        # Write the new data
        with open(file, 'w', encoding='utf-8') as json_file:
            json.dump(data, json_file, indent=4)
        logger.debug(f"Wrote new data to JSON file '{file}'.")
    except FileNotFoundError:
        # If the original file doesn't exist, create it without backup
        with open(file, 'w', encoding='utf-8') as json_file:
            json.dump(data, json_file, indent=4)
        logger.debug(f"Created new JSON file '{file}'.")
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
        self.id = id  # Serial Number or FAA ID
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
        self.description_parts = set()  # Use a set to store unique description parts
        self.time = iso_timestamp_now()  # Last updated time

    def update(self, data: dict):
        """Updates the drone's telemetry data and last seen time."""
        # Update telemetry fields if present
        self.lat = data.get('lat', self.lat)
        self.lon = data.get('lon', self.lon)
        self.speed = data.get('speed', self.speed)
        self.vspeed = data.get('vspeed', self.vspeed)
        self.alt = data.get('alt', self.alt)
        self.height = data.get('height', self.height)
        self.pilot_lat = data.get('pilot_lat', self.pilot_lat)
        self.pilot_lon = data.get('pilot_lon', self.pilot_lon)

        # Update descriptions
        new_description = data.get('description', "")
        if new_description:
            # Split the description into parts using ';' as a delimiter
            parts = [part.strip() for part in new_description.split(';') if part.strip()]
            self.description_parts.update(parts)
            logger.debug(f"Updated description parts for drone '{self.id}': {self.description_parts}")

        # Update the timestamp
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
            "description": "; ".join(sorted(self.description_parts))
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
        Handles only 'Serial Number' and 'CAA Assigned Registration ID' id_types.
        Returns the main drone ID if successful, else None.
        """
        mac = drone_info.get('mac')
        serial_number = drone_info.get('id', None)  # 'id' is 'serial_number' if available
        description = drone_info.get('description', "")

        if not mac:
            logger.debug(f"Drone with ID '{serial_number}' has no MAC address. Skipping...")
            return None

        if serial_number:
            drone_id_full = f"drone-{serial_number}"
        else:
            drone_id_full = "drone-unknown"

        if mac in self.mac_to_drone_id:
            existing_id = self.mac_to_drone_id[mac]
            existing_drone = self.drone_dict[existing_id]

            # Update 'id' if a Serial Number is provided and it's different
            if serial_number and existing_drone.id != drone_id_full:
                # Update 'id'
                existing_drone.id = drone_id_full
                self.drone_dict[drone_id_full] = existing_drone
                del self.drone_dict[existing_id]
                self.mac_to_drone_id[mac] = drone_id_full
                logger.debug(f"Updated drone ID from '{existing_id}' to '{drone_id_full}' based on Serial Number.")

            # Append CAA Assigned Registration ID to description if present
            if description:
                # Assuming description contains only CAA Assigned Registration ID
                # Avoid adding Serial Number or other texts
                existing_drone.update({'description': description})
                logger.debug(f"Appended description to drone '{existing_drone.id}': {description}")

            # Update telemetry
            existing_drone.update(drone_info)
            logger.debug(f"Updated drone '{existing_drone.id}' with MAC '{mac}'.")

            return self.mac_to_drone_id[mac]
        else:
            # New drone, add to manager
            if len(self.drones) >= self.drones.maxlen:
                oldest_mac, oldest_id = self.drones.popleft()
                del self.mac_to_drone_id[oldest_mac]
                del self.drone_dict[oldest_id]
                logger.debug(f"Removed oldest drone '{oldest_id}' with MAC '{oldest_mac}'.")

            new_drone = Drone(id=drone_id_full, mac=mac)
            new_drone.update(drone_info)
            self.drones.append((mac, drone_id_full))
            self.drone_dict[drone_id_full] = new_drone
            self.mac_to_drone_id[mac] = drone_id_full
            logger.debug(f"Added new drone '{drone_id_full}' with MAC '{mac}'.")

            return drone_id_full

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
                # Inherit descriptions from main drone
                if main_drone_id in self.drone_dict:
                    main_drone = self.drone_dict[main_drone_id]
                    new_pilot.description_parts = set(main_drone.description_parts)
                new_pilot.time = drone_info.get('time', iso_timestamp_now())
                self.drones.append((None, pilot_id))  # Pilots don't have a MAC
                self.drone_dict[pilot_id] = new_pilot
                logger.debug(f"Added new pilot drone '{pilot_id}'.")
            else:
                # Update existing pilot drone
                pilot_drone = self.drone_dict[pilot_id]
                pilot_drone.lat = pilot_lat
                pilot_drone.lon = pilot_lon
                pilot_drone.update(drone_info)
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

    def send_updates(self, file: str, create_backup: bool = False):
        """Writes the current drone data to the specified JSON file with optional backup."""
        data_to_write = self.to_json_list()
        try:
            JSONWriter(file, data_to_write, create_backup)
            logger.debug(f"Updated JSON file '{file}' with {len(data_to_write)} drones.")
        except Exception as e:
            logger.error(f"Error writing JSON: {e}")

def parse_list_format(message_list: list) -> dict:
    """Parses Bluetooth formatted drone data (list of dicts) and extracts relevant information including MAC address."""
    drone_info = {}
    drone_info['mac'] = None
    serial_number = None
    caa_id = None
    descriptions = set()  # Use a set to accumulate unique descriptions

    for item in message_list:
        if not isinstance(item, dict):
            logger.debug(f"Unexpected item in list: {item}")
            continue

        # Basic ID
        if 'Basic ID' in item:
            id_type = item['Basic ID'].get('id_type', '').strip().lower()
            current_id = item['Basic ID'].get('id', 'unknown').strip()

            if id_type == 'serial number (ansi/cta-2063-a)':
                serial_number = current_id
                logger.debug(f"Parsed Serial Number: {current_id}")
            elif id_type == 'caa assigned registration id':
                caa_id = current_id
                descriptions.add(caa_id)  # Add only the CAA number
                logger.debug(f"Parsed CAA Assigned Registration ID: {caa_id}")

            # Extract MAC address
            mac = item['Basic ID'].get('MAC', '').strip()
            if mac and is_valid_mac(mac):
                drone_info['mac'] = mac.lower()  # Standardize to lowercase
                logger.debug(f"Parsed MAC address: {mac.lower()}")
            else:
                logger.debug(f"Invalid or missing MAC address in Bluetooth message: '{mac}'.")

        # Operator ID Message (FAA Info) - Excluded from description
        # Commented out to prevent adding to description
        # if 'Operator ID Message' in item:
        #     operator_id_type = item['Operator ID Message'].get('operator_id_type', '').strip().lower()
        #     operator_id = item['Operator ID Message'].get('operator_id', '').strip()
        #     if operator_id_type == 'operator id' and operator_id:
        #         # Add FAA Operator ID to description
        #         descriptions.add(operator_id)
        #         logger.debug(f"Added Operator ID to description: {operator_id}")

        # Location/Vector
        if 'Location/Vector Message' in item:
            loc_vec = item['Location/Vector Message']
            drone_info['lat'] = parse_float(str(loc_vec.get('latitude', "0.0")))
            drone_info['lon'] = parse_float(str(loc_vec.get('longitude', "0.0")))
            drone_info['speed'] = parse_float(str(loc_vec.get('speed', "0.0")))
            drone_info['vspeed'] = parse_float(str(loc_vec.get('vert_speed', "0.0")))
            drone_info['alt'] = parse_float(str(loc_vec.get('geodetic_altitude', "0.0")))
            drone_info['height'] = parse_float(str(loc_vec.get('height_agl', "0.0")))
            logger.debug(f"Parsed Location/Vector Message: lat={drone_info['lat']}, lon={drone_info['lon']}")

        # Self-ID - Excluded from description
        # Commented out to prevent adding to description
        # if 'Self-ID Message' in item:
        #     self_id_text = item['Self-ID Message'].get('text', "").strip()
        #     if self_id_text:
        #         descriptions.add(self_id_text)
        #         logger.debug(f"Added Self-ID Message to description: {self_id_text}")

        # System - Not adding to description
        if 'System Message' in item:
            sysm = item['System Message']
            drone_info['pilot_lat'] = parse_float(sysm.get('latitude', "0.0"))
            drone_info['pilot_lon'] = parse_float(sysm.get('longitude', "0.0"))
            logger.debug(f"Parsed System Message: pilot_lat={drone_info['pilot_lat']}, pilot_lon={drone_info['pilot_lon']}")

    # Combine all descriptions into a single string
    if descriptions:
        drone_info['description'] = "; ".join(sorted(descriptions))
        logger.debug(f"Combined descriptions: {drone_info['description']}")
    else:
        drone_info['description'] = ""

    # Now, set 'id' as 'serial_number' if exists
    if serial_number:
        drone_info['id'] = serial_number
        drone_info['id_type'] = 'Serial Number'

    return drone_info

def parse_esp32_dict(message: dict) -> dict:
    """Parses ESP32 formatted drone data (single dict) and extracts relevant information including MAC address."""
    drone_info = {}
    descriptions = set()  # Use a set to accumulate unique descriptions

    # Check for 'Basic ID'
    if 'Basic ID' in message:
        id_type = message['Basic ID'].get('id_type', '').strip().lower()
        if id_type == 'serial number (ansi/cta-2063-a)':
            drone_info['id'] = message['Basic ID'].get('id', 'unknown').strip()
            drone_info['id_type'] = 'Serial Number'
            logger.debug(f"Parsed Serial Number: {drone_info['id']}")
        elif id_type == 'caa assigned registration id':
            caa_assigned_number = message['Basic ID'].get('id', 'unknown').strip()
            descriptions.add(caa_assigned_number)  # Add only the CAA number
            drone_info['id_type'] = 'CAA Assigned'
            # Do not set 'id' for CAA Assigned ID
            logger.debug(f"Parsed CAA Assigned Registration ID: {caa_assigned_number}")

        # Extract MAC address
        mac = message['Basic ID'].get('MAC', '').strip()
        if mac and is_valid_mac(mac):
            drone_info['mac'] = mac.lower()  # Standardize to lowercase
            logger.debug(f"Parsed MAC address: {mac.lower()}")
        else:
            logger.debug(f"Invalid or missing MAC address in ESP32 message: '{mac}'.")

    # Custom 'drone_id' key (if exists and 'id' not already set) - Excluded from description
    # Commented out to prevent adding to description
    # if 'drone_id' in message and 'id' not in drone_info:
    #     drone_info['id'] = message['drone_id'].strip()
    #     drone_info['id_type'] = 'Other'
    #     logger.debug(f"Parsed custom drone_id: {drone_info['id']}")

    # Parse location data
    if 'latitude' in message:
        drone_info['lat'] = parse_float(str(message['latitude']))
        logger.debug(f"Parsed latitude: {drone_info['lat']}")
    if 'longitude' in message:
        drone_info['lon'] = parse_float(str(message['longitude']))
        logger.debug(f"Parsed longitude: {drone_info['lon']}")
    if 'altitude' in message:
        drone_info['alt'] = parse_float(str(message['altitude']))
        logger.debug(f"Parsed altitude: {drone_info['alt']}")
    if 'speed' in message:
        drone_info['speed'] = parse_float(str(message['speed']))
        logger.debug(f"Parsed speed: {drone_info['speed']}")
    if 'vert_speed' in message:
        drone_info['vspeed'] = parse_float(str(message['vert_speed']))
        logger.debug(f"Parsed vertical speed: {drone_info['vspeed']}")
    if 'height' in message:
        drone_info['height'] = parse_float(str(message['height']))
        logger.debug(f"Parsed height: {drone_info['height']}")

    # Pilot lat/lon
    if 'pilot_lat' in message:
        drone_info['pilot_lat'] = parse_float(str(message['pilot_lat']))
        logger.debug(f"Parsed pilot_lat: {drone_info['pilot_lat']}")
    if 'pilot_lon' in message:
        drone_info['pilot_lon'] = parse_float(str(message['pilot_lon']))
        logger.debug(f"Parsed pilot_lon: {drone_info['pilot_lon']}")

    # Top-level 'description' - Excluded from description
    # Commented out to prevent adding to description
    # if 'description' in message:
    #     descriptions.add(message['description'].strip())
    #     logger.debug(f"Added top-level description: {message['description'].strip()}")
    # else:
    #     # Append 'Self-ID Message' if exists
    #     self_id_text = message.get('Self-ID Message', {}).get('text', "").strip()
    #     if self_id_text:
    #         descriptions.add(self_id_text)
    #         logger.debug(f"Added Self-ID Message to description: {self_id_text}")

    # Combine all descriptions into a single string
    if descriptions:
        drone_info['description'] = "; ".join(sorted(descriptions))
        logger.debug(f"Combined descriptions: {drone_info['description']}")
    else:
        drone_info['description'] = ""

    # Now, set 'id' as 'serial_number' if exists
    if 'id' in drone_info and drone_info['id']:
        drone_info['id_type'] = 'Serial Number'

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

    # <<< ADDED CODE: Create the JSON file (empty) on script load if it doesn't exist. >>>
    if not os.path.exists(file):
        JSONWriter(file, [], create_backup=False)
        logger.debug(f"Created empty JSON file '{file}' at script start.")

    while True:
        try:
            message = zmq_socket.recv_json()
            logger.debug(f"Received message: {message}")
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

        # Debug: Verify drone_info contents
        logger.debug(f"Drone Info before condition: id={drone_info.get('id')}, mac={drone_info.get('mac')}")

        # Ensure 'mac' is present
        if 'mac' in drone_info and drone_info['mac']:
            # Add a 'time' field in ISO8601 for tar1090 ingestion
            drone_info['time'] = iso_timestamp_now()
            logger.debug(f"Added timestamp: {drone_info['time']}")

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
                    logger.info(f"Skipping drone {drone_info.get('id')} - invalid lat/lon: ({main_lat}, {main_lon})")
                    # Remove any existing pilot entry
                    drone_manager.update_or_add_pilot_drone(main_drone_id, {'pilot_lat': 0.0, 'pilot_lon': 0.0, 'time': drone_info['time']})
                    continue

                # Update or add the pilot drone based on pilot coordinates
                drone_manager.update_or_add_pilot_drone(main_drone_id, drone_info)

            # After updating, write JSON
            # Create backup only if verbose logging is enabled
            create_backup = logger.level == logging.DEBUG
            drone_manager.send_updates(file, create_backup=create_backup)

            # Remove any drones that haven't been updated for > max_age seconds
            drone_manager.remove_old_drones(max_age)
        else:
            logger.debug("No valid 'mac' found in message. Skipping...")

        # Optional: Throttle burst processing to prevent rapid overwriting
        # time.sleep(0.1)  # 100 milliseconds delay

def main():
    parser = argparse.ArgumentParser(description="ZMQ to JSON for tar1090, handling standard & ESP32 formats with MAC-based consolidation.")
    parser.add_argument("--zmqsetting", default="127.0.0.1:4224", help="Define ZMQ server to connect to (default=127.0.0.1:4224)")
    parser.add_argument("--json-file", default="/run/readsb/drone.json", help="JSON file to write parsed data to. (default=/run/readsb/drone.json)")
    parser.add_argument("--max-age", default=10, help="Number of seconds before drone is old and removed from JSON file (default=10)", type=float)
    parser.add_argument("--max-drones", default=30, help="Maximum number of drones to track (default=30)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()

    # Configure logging level and format
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    logger.info(f"Starting ZMQ to JSON with log level: {'DEBUG' if args.verbose else 'WARNING'}")

    zmq_to_json(args.zmqsetting, args.json_file, args.max_age, args.max_drones)

if __name__ == "__main__":
    main()
