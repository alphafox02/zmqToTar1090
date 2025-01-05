#!/usr/bin/env python3
"""
DjiToTar1090.py

Author: CemaXecuter
Description: Connects to AntSDR to receive DJI DroneID data, parses it,
             and writes it to /run/readsb/dji_drone.json in a format compatible with tar1090.
             Includes pilot information only if valid.
Usage:
    python3 DjiToTar1090.py [-d]

Options:
    -d, --debug    Enable debug mode for verbose output and raw data logging.

Requirements:
    - Python 3.6+
    - Write permissions to /run/readsb/dji_drone.json
"""

import socket
import struct
import json
import time
import datetime
import logging
import os
import sys
import threading
import argparse
import signal

# Configuration Constants
ANTSDR_IP = "192.168.1.10"                   # Default AntSDR IP
ANTSDR_PORT = 41030                          # Default AntSDR Port
JSON_FILE_PATH = "/run/readsb/dji_drone.json"  # Output JSON file
RECONNECT_DELAY = 5                           # Seconds to wait before reconnecting
WRITE_INTERVAL = 1                            # Seconds between JSON writes

# Shared data structures
drones = {}
pilots = {}

def setup_logging(debug: bool):
    """
    Configures logging based on the debug flag.

    Args:
        debug (bool): If True, sets logging level to DEBUG and logs to console.
                      If False, sets logging level to WARNING and logs to console.
    """
    log_level = logging.DEBUG if debug else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[logging.StreamHandler(sys.stdout)]
    )

def iso_timestamp_now() -> str:
    """Return current UTC time as an ISO8601 string with 'Z' suffix."""
    return datetime.datetime.utcnow().isoformat(timespec='milliseconds') + 'Z'

def is_valid_latlon(lat: float, lon: float) -> bool:
    """Check if latitude and longitude are within valid ranges and not zero."""
    return (-90.0 <= lat <= 90.0 and lat != 0.0) and (-180.0 <= lon <= 180.0 and lon != 0.0)

def write_atomic(file_path: str, data: list):
    """
    Writes data to a JSON file atomically to prevent data corruption.
    Writes to a temporary file first and then renames it.

    Args:
        file_path (str): Path to the target JSON file.
        data (list): List of dictionaries containing drone and pilot data.
    """
    temp_file = f"{file_path}.tmp"
    try:
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
        os.replace(temp_file, file_path)
        logging.debug(f"Successfully wrote data to {file_path}")
    except Exception as e:
        logging.error(f"Failed to write JSON data: {e}")

def parse_frame(frame):
    frame_header = frame[:2]
    package_type = frame[2]
    length_bytes = frame[3:5]
    # struct.unpack parse uint16_t
    package_length = struct.unpack('H', length_bytes)[0]
    logging.debug(f"package_length: {package_length}")
    data = frame[5:5 + package_length - 5]
    return package_type, data

def parse_data_1(data):
    try:
        serial_number = data[:64].decode('utf-8').rstrip('\x00')
        device_type = data[64:128].decode('utf-8').rstrip('\x00')
        device_type_8 = data[128]
        app_lat = struct.unpack('d', data[129:137])[0]
        app_lon = struct.unpack('d', data[137:145])[0]
        drone_lat = struct.unpack('d', data[145:153])[0]
        drone_lon = struct.unpack('d', data[153:161])[0]
        height = struct.unpack('d', data[161:169])[0]
        altitude = struct.unpack('d', data[169:177])[0]
        home_lat = struct.unpack('d', data[177:185])[0]
        home_lon = struct.unpack('d', data[185:193])[0]
        freq = struct.unpack('d', data[193:201])[0]
        speed_E = struct.unpack('d', data[201:209])[0]
        speed_N = struct.unpack('d', data[209:217])[0]
        speed_U = struct.unpack('d', data[217:225])[0]
        rssi = struct.unpack('h', data[225:227])[0]  # Corrected slicing to 2 bytes
    except UnicodeDecodeError:
        device_type = "DJI Drone"
        device_type_8 = 255
        # Initialize all other fields to default values to prevent NameError
        serial_number = "Unknown"
        app_lat = app_lon = drone_lat = drone_lon = height = altitude = home_lat = home_lon = freq = speed_E = speed_N = speed_U = rssi = 0

    return {
        'serial_number': serial_number,
        'device_type': device_type,
        'device_type_8': device_type_8,
        'app_lat': app_lat,
        'app_lon': app_lon,
        'drone_lat': drone_lat,
        'drone_lon': drone_lon,
        'height': height,
        'altitude': altitude,
        'home_lat': home_lat,
        'home_lon': home_lon,
        'freq': freq,
        'speed_E': speed_E,
        'speed_N': speed_N,
        'speed_U': speed_U,
        'RSSI': rssi
    }

def tcp_client(debug: bool):
    """
    Connects to AntSDR, receives data, parses it, and updates the drones and pilots dictionaries.

    Args:
        debug (bool): If True, enables detailed logging.
    """
    server_ip = ANTSDR_IP
    server_port = ANTSDR_PORT

    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client_socket.connect((server_ip, server_port))
        logging.debug(f"Connected to server {server_ip}:{server_port}")

        while True:
            frame = client_socket.recv(1024)
            if not frame:
                logging.warning("Connection closed by AntSDR.")
                break
            package_type, data = parse_frame(frame)
            if package_type == 0x01:
                parsed_data = parse_data_1(data)
                logging.debug("*****************")
                logging.debug(f"Package Type: {package_type}")
                for key, value in parsed_data.items():
                    logging.debug(f"{key}: {value}")
                logging.debug("*****************\n")

                # Update drones dictionary
                serial = parsed_data["serial_number"]
                drones[serial] = parsed_data

                # Handle pilot data if available (app_lat and app_lon)
                if is_valid_latlon(parsed_data["app_lat"], parsed_data["app_lon"]):
                    pilot_id = f"pilot-{serial}"
                    pilots[pilot_id] = {
                        "id": pilot_id,
                        "callsign": serial,  # Using serial number as callsign
                        "time": iso_timestamp_now(),
                        "lat": parsed_data["app_lat"],
                        "lon": parsed_data["app_lon"],
                        "speed": 0,          # Assuming no speed data for pilot
                        "vspeed": 0,         # Assuming no vertical speed data for pilot
                        "alt": parsed_data["altitude"],
                        "height": parsed_data["height"],
                        "description": parsed_data["device_type"] if parsed_data["device_type"] else "DJI Drone",
                        "RSSI": parsed_data["RSSI"]
                    }
                    logging.debug(f"Pilot Data - {pilot_id}: {pilots[pilot_id]}")
                else:
                    # If no valid pilot data, remove existing pilot entry if any
                    pilot_id = f"pilot-{serial}"
                    if pilot_id in pilots:
                        del pilots[pilot_id]
                        logging.debug(f"Removed Pilot Data - {pilot_id}")
    except Exception as e:
        logging.debug(f"recv error: {e}")
    finally:
        client_socket.close()
        logging.debug("Disconnected from AntSDR.")

def parse_args():
    """
    Parses command-line arguments.

    Returns:
        argparse.Namespace: Parsed arguments containing debug flag.
    """
    parser = argparse.ArgumentParser(description="Connect to AntSDR, parse DJI DroneID data, and output to tar1090-compatible JSON.")
    parser.add_argument('-d', '--debug', action='store_true',
                        help='Enable debug mode for verbose output and raw data logging.')
    return parser.parse_args()

def handle_shutdown(signum, frame):
    """
    Handles shutdown signals for graceful exit.

    Args:
        signum (int): Signal number.
        frame: Current stack frame.
    """
    logging.info("Shutdown signal received. Exiting gracefully...")
    sys.exit(0)

def main():
    """
    Main function to initialize drone and pilot data collection and JSON writing.
    """
    args = parse_args()
    setup_logging(args.debug)

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, handle_shutdown)   # Handle Ctrl+C
    signal.signal(signal.SIGTERM, handle_shutdown)  # Handle termination signals

    # Start the TCP client in a separate thread
    client_thread = threading.Thread(
        target=tcp_client,
        args=(args.debug,),
        daemon=True
    )
    client_thread.start()
    logging.info("Started TCP client thread.")

    # Main loop to periodically write drones and pilots data to JSON
    try:
        while True:
            # Combine drones and pilots into a single list
            combined_data = []

            # Add drone entries
            for drone in drones.values():
                drone_entry = {
                    "id": drone["serial_number"],
                    "callsign": drone["serial_number"],
                    "time": iso_timestamp_now(),
                    "lat": drone["drone_lat"],
                    "lon": drone["drone_lon"],
                    "speed": 0,          # Assuming no speed data for drone
                    "vspeed": 0,         # Assuming no vertical speed data for drone
                    "alt": drone["altitude"],
                    "height": drone["height"],
                    "description": drone["device_type"] if drone["device_type"] else "DJI Drone",
                    "RSSI": drone["RSSI"]  # Added RSSI to drone entry
                }
                combined_data.append(drone_entry)

            # Add pilot entries only if they exist and have valid coordinates
            for pilot in list(pilots.values()):
                # Double-check that pilot lat and lon are valid
                if is_valid_latlon(pilot["lat"], pilot["lon"]):
                    pilot_entry = {
                        "id": pilot["id"],
                        "callsign": pilot["callsign"],
                        "time": pilot["time"],
                        "lat": pilot["lat"],
                        "lon": pilot["lon"],
                        "speed": pilot["speed"],
                        "vspeed": pilot["vspeed"],
                        "alt": pilot["alt"],
                        "height": pilot["height"],
                        "description": pilot["description"] if pilot["description"] else "DJI Drone",
                        "RSSI": pilot["RSSI"]
                    }
                    combined_data.append(pilot_entry)
                else:
                    # If pilot coordinates are invalid, ensure they are not in pilots dict
                    pilot_id = pilot["id"]
                    if pilot_id in pilots:
                        del pilots[pilot_id]
                        logging.debug(f"Removed invalid Pilot Data - {pilot_id}")

            # Write the combined data to JSON atomically
            write_atomic(JSON_FILE_PATH, combined_data)
            logging.debug(f"Wrote {len(drones)} drones and {len(pilots)} pilots to JSON.")
            time.sleep(WRITE_INTERVAL)
    except KeyboardInterrupt:
        logging.info("Script interrupted by user. Exiting...")
        sys.exit(0)
    except Exception as e:
        logging.exception(f"Unexpected error in main loop: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
