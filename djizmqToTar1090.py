#!/usr/bin/env python3
"""
DjiToTar1090.py

Author: CemaXecuter
Description:
    Subscribes to the ZMQ XPUB socket from dji_receiver.py to receive
    DJI DroneID data. Parses pilot location (from "System Message") and
    drone coordinates (from "Location/Vector Message"), storing them in
    a JSON file that's compatible with tar1090.

Features:
    - Takes a ZMQ endpoint (default: tcp://127.0.0.1:4221) for DJI data.
    - Tracks and writes drone + pilot information to /run/readsb/dji_drone.json.
    - Removes stale entries after a configurable max_age.
    - Performs atomic JSON writes to prevent file corruption.
    - Provides graceful shutdown on SIGINT/SIGTERM.

Usage:
    python3 DjiToTar1090.py [-d] [--max-age 10] [--dji-url tcp://127.0.0.1:4221]

Options:
    -d, --debug          Enable debug mode for verbose output.
    --max-age <seconds>  Number of seconds before removing stale entries (default=10).
    --dji-url <zmq_url>  ZMQ XPUB URL from dji_receiver.py (default=tcp://127.0.0.1:4221).

Requirements:
    - Python 3.6+
    - Write permissions to /run/readsb/dji_drone.json
"""

import zmq
import json
import argparse
import signal
import sys
import datetime
import time
import logging
import os
import threading
from threading import Lock

# Configuration Constants
JSON_FILE_PATH = "/run/readsb/dji_drone.json"  # Output JSON file
WRITE_INTERVAL = 1                             # Seconds between JSON writes

# Global data structures (shared, protected by locks)
drones = {}
pilots = {}
drones_lock = Lock()
pilots_lock = Lock()

def setup_logging(debug: bool):
    """
    Sets up logging behavior. Debug mode prints more details to console.

    Args:
        debug (bool): If True, sets level to DEBUG. Otherwise, WARNING.
    """
    log_level = logging.DEBUG if debug else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

def iso_timestamp_now() -> str:
    """
    Returns the current UTC time as an ISO8601 string with 'Z' suffix.
    Example: "2025-01-04T12:34:52.123Z"
    """
    return datetime.datetime.utcnow().isoformat(timespec='milliseconds') + 'Z'

def is_valid_latlon(lat: float, lon: float) -> bool:
    """
    Checks if latitude and longitude are within valid ranges and
    not zero, indicating a meaningful location.

    Args:
        lat (float): Latitude in degrees.
        lon (float): Longitude in degrees.

    Returns:
        bool: True if valid, False otherwise.
    """
    return (-90.0 <= lat <= 90.0 and lat != 0.0) and (-180.0 <= lon <= 180.0 and lon != 0.0)

def write_atomic(file_path: str, data: list):
    """
    Writes the data list to file_path atomically.
    Writes to a temporary file, then renames it, preventing corruption.

    Args:
        file_path (str): Target JSON file to write.
        data (list): The list of drone + pilot dictionaries to write.
    """
    temp_file = f"{file_path}.tmp"
    try:
        with open(temp_file, 'w', encoding='utf-8') as tmp:
            json.dump(data, tmp, indent=4)
        os.replace(temp_file, file_path)
        logging.debug(f"Atomically wrote new data to {file_path}")
    except Exception as e:
        logging.error(f"Failed to write JSON data: {e}")

def handle_shutdown(signum, frame):
    """
    Handles shutdown signals (SIGINT/SIGTERM) for a graceful exit.

    Args:
        signum (int): The signal number.
        frame: Current stack frame.
    """
    logging.info("Shutdown signal received. Exiting gracefully...")
    sys.exit(0)

def cleanup_stale_entries(max_age: float):
    """
    Removes drones and pilots that haven't been seen within `max_age` seconds.

    Args:
        max_age (float): Time in seconds before an entry is considered stale.
    """
    current_time = time.time()

    # Cleanup drones
    with drones_lock:
        stale_drones = [
            serial for serial, info in drones.items()
            if (current_time - info["last_seen"]) > max_age
        ]
        for serial in stale_drones:
            del drones[serial]
            logging.debug(f"Removed stale drone: {serial}")

    # Cleanup pilots
    with pilots_lock:
        stale_pilots = [
            pilot_id for pilot_id, info in pilots.items()
            if (current_time - info["last_seen"]) > max_age
        ]
        for pilot_id in stale_pilots:
            del pilots[pilot_id]
            logging.debug(f"Removed stale pilot: {pilot_id}")

def parse_dji_list_format(message_list: list) -> dict:
    """
    Parses a list of dictionaries as produced by dji_receiver.py.
    Example message_list:
    [
      {"Basic ID": {...}},
      {"Location/Vector Message": {...}},
      {"Self-ID Message": {...}},
      {"System Message": {...}}
    ]

    Returns a dictionary with fields:
    {
        "serial_number": <string>,
        "device_type": <string>,
        "drone_lat": <float>,
        "drone_lon": <float>,
        "height": <float>,
        "altitude": <float>,
        "pilot_lat": <float>,
        "pilot_lon": <float>
    }
    """
    drone_info = {
        "serial_number": "unknown",
        "device_type": "DJI Drone",
        "drone_lat": 0.0,
        "drone_lon": 0.0,
        "height": 0.0,
        "altitude": 0.0,
        "pilot_lat": 0.0,
        "pilot_lon": 0.0
    }

    for item in message_list:
        if "Basic ID" in item:
            basic_id = item["Basic ID"]
            if basic_id.get("id_type") == "Serial Number (ANSI/CTA-2063-A)":
                drone_info["serial_number"] = basic_id.get("id", "unknown")
                drone_info["device_type"] = basic_id.get("description", "DJI Drone")

        elif "Location/Vector Message" in item:
            loc_vec = item["Location/Vector Message"]
            drone_info["drone_lat"] = loc_vec.get("latitude", 0.0)
            drone_info["drone_lon"] = loc_vec.get("longitude", 0.0)
            drone_info["height"] = loc_vec.get("height_agl", 0.0)
            drone_info["altitude"] = loc_vec.get("geodetic_altitude", 0.0)

        elif "System Message" in item:
            sysm = item["System Message"]
            drone_info["pilot_lat"] = sysm.get("latitude", 0.0)
            drone_info["pilot_lon"] = sysm.get("longitude", 0.0)

    return drone_info

def dji_subscriber_thread(dji_url: str):
    """
    Subscribes to the dji_receiver.py's XPUB socket at dji_url.
    Reads JSON strings, each a list of dictionaries describing a single drone update.

    Args:
        dji_url (str): The ZMQ XPUB endpoint (e.g. "tcp://127.0.0.1:4221").
    """
    context = zmq.Context()
    socket = context.socket(zmq.SUB)
    socket.setsockopt_string(zmq.SUBSCRIBE, "")
    socket.connect(dji_url)

    while True:
        try:
            message = socket.recv_string()
            data = json.loads(message)  # Should be a list of dicts
            if isinstance(data, list):
                parsed = parse_dji_list_format(data)
                serial = parsed["serial_number"]
                current_time = time.time()

                # Update main drone dictionary
                with drones_lock:
                    drones[serial] = {
                        "data": parsed,
                        "last_seen": current_time
                    }

                # Update or remove pilot if needed
                if is_valid_latlon(parsed["pilot_lat"], parsed["pilot_lon"]):
                    pilot_id = f"pilot-{serial}"
                    with pilots_lock:
                        pilots[pilot_id] = {
                            "id": pilot_id,
                            "callsign": serial,
                            "time": iso_timestamp_now(),
                            "lat": parsed["pilot_lat"],
                            "lon": parsed["pilot_lon"],
                            "speed": 0.0,
                            "vspeed": 0.0,
                            "alt": parsed["altitude"],
                            "height": parsed["height"],
                            "description": parsed["device_type"],
                            "RSSI": 0.0,  # Not provided from dji_receiver
                            "last_seen": current_time
                        }
                else:
                    # If pilot coords invalid, remove any existing pilot entry
                    pilot_id = f"pilot-{serial}"
                    with pilots_lock:
                        if pilot_id in pilots:
                            del pilots[pilot_id]
                            logging.debug(f"Removed pilot data for {pilot_id}")

        except Exception as e:
            logging.error(f"Error reading from DJI ZMQ subscription: {e}")

def main():
    parser = argparse.ArgumentParser(description="DjiToTar1090: Subscribes to dji_receiver's ZMQ data.")
    parser.add_argument('-d', '--debug', action='store_true',
                        help='Enable debug mode for verbose output.')
    parser.add_argument('--max-age', type=float, default=10,
                        help='Seconds before stale entries are removed (default=10).')
    parser.add_argument('--dji-url', default="tcp://127.0.0.1:4221",
                        help='ZMQ XPUB url from dji_receiver.py (default=tcp://127.0.0.1:4221)')
    args = parser.parse_args()

    setup_logging(args.debug)

    # Graceful shutdown on SIGINT, SIGTERM
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    # Start the ZMQ subscriber thread
    sub_thread = threading.Thread(target=dji_subscriber_thread, args=(args.dji_url,), daemon=True)
    sub_thread.start()
    logging.info(f"Started DJI subscriber thread for {args.dji_url}")

    try:
        while True:
            # Remove stale drones/pilots
            cleanup_stale_entries(args.max_age)

            # Build combined data
            combined_data = []

            # Lock drones
            with drones_lock:
                for drone_info in drones.values():
                    drone = drone_info["data"]
                    serial = drone["serial_number"]
                    # Build a drone entry
                    combined_data.append({
                        "id": serial,
                        "callsign": serial,
                        "time": iso_timestamp_now(),
                        "lat": drone.get("drone_lat", 0.0),
                        "lon": drone.get("drone_lon", 0.0),
                        "speed": 0.0,  # not provided
                        "vspeed": 0.0,  # not provided
                        "alt": drone.get("altitude", 0.0),
                        "height": drone.get("height", 0.0),
                        "description": drone.get("device_type", "DJI Drone"),
                        "rssi": 0.0  # not provided
                    })

            # Lock pilots
            with pilots_lock:
                for pilot_id, pilot_info in pilots.items():
                    lat = pilot_info["lat"]
                    lon = pilot_info["lon"]
                    if is_valid_latlon(lat, lon):
                        combined_data.append({
                            "id": pilot_id,
                            "callsign": pilot_info["callsign"],
                            "time": pilot_info["time"],
                            "lat": lat,
                            "lon": lon,
                            "speed": pilot_info["speed"],
                            "vspeed": pilot_info["vspeed"],
                            "alt": pilot_info["alt"],
                            "height": pilot_info["height"],
                            "description": pilot_info["description"],
                            "rssi": pilot_info["RSSI"]
                        })
                    else:
                        # Remove invalid pilot coords
                        del pilots[pilot_id]
                        logging.debug(f"Removed invalid pilot data for {pilot_id}")

            # Atomically write the JSON
            write_atomic(JSON_FILE_PATH, combined_data)
            logging.debug(f"Wrote {len(drones)} drones and {len(pilots)} pilots to JSON.")

            time.sleep(WRITE_INTERVAL)

    except KeyboardInterrupt:
        logging.info("User interruption (Ctrl-C). Exiting...")
    except Exception as e:
        logging.exception(f"Fatal error in main loop: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
