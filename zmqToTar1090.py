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
import os

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

def initialize_json_file(file: str):
    """Ensure the JSON file exists by creating an empty structure if needed."""
    if not os.path.exists(file):
        logger.info(f"{file} does not exist. Creating an initial empty JSON file.")
        JSONWriter(file, [])  # Write an empty list as the initial structure

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
        self.lat = data.get('lat', self.lat)
        self.lon = data.get('lon', self.lon)
        self.speed = data.get('speed', self.speed)
        self.vspeed = data.get('vspeed', self.vspeed)
        self.alt = data.get('alt', self.alt)
        self.height = data.get('height', self.height)
        self.pilot_lat = data.get('pilot_lat', self.pilot_lat)
        self.pilot_lon = data.get('pilot_lon', self.pilot_lon)
        new_description = data.get('description', "")
        if new_description:
            parts = [part.strip() for part in new_description.split(';') if part.strip()]
            self.description_parts.update(parts)
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
        if self.pilot_lat != 0.0 and self.pilot_lon != 0.0:
            drone_dict["pilot_lat"] = self.pilot_lat
            drone_dict["pilot_lon"] = self.pilot_lon
        return drone_dict

class DroneManager:
    """Manages a collection of drones and handles their updates based on MAC address."""
    def __init__(self, max_drones=30):
        self.drones = deque(maxlen=max_drones)
        self.mac_to_drone_id = {}
        self.drone_dict = {}

    def update_or_add_main_drone(self, drone_info: dict) -> str:
        mac = drone_info.get('mac')
        serial_number = drone_info.get('id', None)
        description = drone_info.get('description', "")
        if not mac:
            return None
        if serial_number:
            drone_id_full = f"drone-{serial_number}"
        else:
            drone_id_full = "drone-unknown"
        if mac in self.mac_to_drone_id:
            existing_id = self.mac_to_drone_id[mac]
            existing_drone = self.drone_dict[existing_id]
            if serial_number and existing_drone.id != drone_id_full:
                existing_drone.id = drone_id_full
                self.drone_dict[drone_id_full] = existing_drone
                del self.drone_dict[existing_id]
                self.mac_to_drone_id[mac] = drone_id_full
            if description:
                existing_drone.update({'description': description})
            existing_drone.update(drone_info)
            return self.mac_to_drone_id[mac]
        else:
            if len(self.drones) >= self.drones.maxlen:
                oldest_mac, oldest_id = self.drones.popleft()
                del self.mac_to_drone_id[oldest_mac]
                del self.drone_dict[oldest_id]
            new_drone = Drone(id=drone_id_full, mac=mac)
            new_drone.update(drone_info)
            self.drones.append((mac, drone_id_full))
            self.drone_dict[drone_id_full] = new_drone
            self.mac_to_drone_id[mac] = drone_id_full
            return drone_id_full

    def update_or_add_pilot_drone(self, main_drone_id: str, drone_info: dict):
        pilot_id = f"pilot-{main_drone_id}"
        pilot_lat = drone_info.get('pilot_lat', 0.0)
        pilot_lon = drone_info.get('pilot_lon', 0.0)
        if is_valid_latlon(pilot_lat, pilot_lon):
            if pilot_id not in self.drone_dict:
                new_pilot = Drone(id=pilot_id)
                new_pilot.lat = pilot_lat
                new_pilot.lon = pilot_lon
                if main_drone_id in self.drone_dict:
                    main_drone = self.drone_dict[main_drone_id]
                    new_pilot.description_parts = set(main_drone.description_parts)
                new_pilot.time = drone_info.get('time', iso_timestamp_now())
                self.drones.append((None, pilot_id))
                self.drone_dict[pilot_id] = new_pilot
            else:
                pilot_drone = self.drone_dict[pilot_id]
                pilot_drone.lat = pilot_lat
                pilot_drone.lon = pilot_lon
                pilot_drone.update(drone_info)
        else:
            if pilot_id in self.drone_dict:
                self.drones = deque([(m, d) for (m, d) in self.drones if d != pilot_id], maxlen=self.drones.maxlen)
                del self.drone_dict[pilot_id]

    def remove_old_drones(self, max_age: float):
        now_ts = time.time()
        remove_list = []
        for drone_id, drone in list(self.drone_dict.items()):
            if drone.id.startswith("pilot-"):
                continue
            try:
                dt = datetime.datetime.fromisoformat(drone.time.replace('Z', '+00:00'))
                last_seen_ts = dt.timestamp()
            except ValueError:
                remove_list.append(drone_id)
                continue
            if (now_ts - last_seen_ts) > max_age:
                remove_list.append(drone_id)
        for drone_id in remove_list:
            drone = self.drone_dict[drone_id]
            mac = None
            for m, d_id in self.mac_to_drone_id.items():
                if d_id == drone_id:
                    mac = m
                    break
            if mac:
                self.drones = deque([(m, d) for (m, d) in self.drones if m != mac], maxlen=self.drones.maxlen)
                del self.mac_to_drone_id[mac]
            del self.drone_dict[drone_id]
            pilot_id = f"pilot-{drone_id}"
            if pilot_id in self.drone_dict:
                self.drones = deque([(m, d) for (m, d) in self.drones if d != pilot_id], maxlen=self.drones.maxlen)
                del self.drone_dict[pilot_id]

    def to_json_list(self) -> list:
        return [drone.to_dict() for drone in self.drone_dict.values()]

    def send_updates(self, file: str, create_backup: bool = False):
        data_to_write = self.to_json_list()
        JSONWriter(file, data_to_write, create_backup=create_backup)

def zmq_to_json(zmqsetting: str, file: str, max_age: float, max_drones: int = 30):
    context = zmq.Context()
    zmq_socket = context.socket(zmq.SUB)
    zmq_socket.connect(f"tcp://{zmqsetting}")
    zmq_socket.setsockopt_string(zmq.SUBSCRIBE, "")
    drone_manager = DroneManager(max_drones=max_drones)
    def signal_handler(sig, frame):
        zmq_socket.close()
        context.term()
        sys.exit(0)
    signal.signal(signal.SIGINT, signal_handler)
    initialize_json_file(file)
    while True:
        try:
            message = zmq_socket.recv_json()
        except Exception:
            continue
        if isinstance(message, list):
            drone_info = parse_list_format(message)
        elif isinstance(message, dict):
            drone_info = parse_esp32_dict(message)
        else:
            continue
        if 'mac' in drone_info and drone_info['mac']:
            drone_info['time'] = iso_timestamp_now()
            main_drone_id = drone_manager.update_or_add_main_drone(drone_info)
            if main_drone_id:
                drone_manager.update_or_add_pilot_drone(main_drone_id, drone_info)
            drone_manager.send_updates(file, create_backup=(logger.level == logging.DEBUG))
            drone_manager.remove_old_drones(max_age)

def main():
    parser = argparse.ArgumentParser(description="ZMQ to JSON for tar1090.")
    parser.add_argument("--zmqsetting", default="127.0.0.1:4224", help="Define ZMQ server to connect to")
    parser.add_argument("--json-file", default="/run/readsb/drone.json", help="JSON file to write parsed data to.")
    parser.add_argument("--max-age", default=10, help="Max age before drone is removed (default=10)", type=float)
    parser.add_argument("--max-drones", default=30, help="Maximum drones to track (default=30)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format='%(asctime)s - %(levelname)s - %(message)s')
    zmq_to_json(args.zmqsetting, args.json_file, args.max_age, args.max_drones)

if __name__ == "__main__":
    main()
