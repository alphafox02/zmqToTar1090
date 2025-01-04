#!/usr/bin/env python3
"""
DjiToTar1090.py

Author: CemaXecuter
Description: Connects to AntSDR to receive DJI DroneID data, parses it,
             and writes it to /run/readsb/dji_drone.json in a format compatible with tar1090.

Usage:
    python3 DjiToTar1090.py

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

# Configuration Constants
ANTSDR_IP = "192.168.1.10"               # Default AntSDR IP
ANTSDR_PORT = 41030                       # Default AntSDR Port
JSON_FILE_PATH = "/run/readsb/dji_drone.json"  # Output JSON file
RECONNECT_DELAY = 5                       # Seconds to wait before reconnecting
WRITE_INTERVAL = 1                        # Seconds between JSON writes

# Setup Logging to Console Only
logging.basicConfig(
    level=logging.INFO,  # Set to DEBUG for more detailed logs
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def iso_timestamp_now() -> str:
    """Return current UTC time as an ISO8601 string with 'Z' suffix."""
    return datetime.datetime.utcnow().isoformat(timespec='milliseconds') + 'Z'

def is_valid_latlon(lat: float, lon: float) -> bool:
    """Check if latitude and longitude are within valid ranges."""
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0

def write_atomic(file_path: str, data: list):
    """
    Writes data to a JSON file atomically to prevent data corruption.
    Writes to a temporary file first and then renames it.
    """
    temp_file = f"{file_path}.tmp"
    try:
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
        os.replace(temp_file, file_path)
        logging.debug(f"Successfully wrote data to {file_path}")
    except Exception as e:
        logging.error(f"Failed to write JSON data: {e}")

def parse_dji_data(data: bytes) -> dict:
    """
    Parses binary DJI DroneID data from AntSDR.

    Expected Data Structure:
    - Serial Number: bytes 0-63 (64 bytes, UTF-8)
    - Device Type: bytes 64-127 (64 bytes, UTF-8)
    - Device Type 8: byte 128 (1 byte)
    - App Lat: bytes 129-136 (8 bytes, double)
    - App Lon: bytes 137-144 (8 bytes, double)
    - Drone Lat: bytes 145-152 (8 bytes, double)
    - Drone Lon: bytes 153-160 (8 bytes, double)
    - Height: bytes 161-168 (8 bytes, double)
    - Altitude: bytes 169-176 (8 bytes, double)
    - Home Lat: bytes 177-184 (8 bytes, double)
    - Home Lon: bytes 185-192 (8 bytes, double)
    - Freq: bytes 193-200 (8 bytes, double)
    - Speed E: bytes 201-208 (8 bytes, double)
    - Speed N: bytes 209-216 (8 bytes, double)
    - Speed U: bytes 217-224 (8 bytes, double)
    - RSSI: bytes 225-226 (2 bytes, short)
    """
    try:
        serial_number = data[0:64].decode('utf-8').rstrip('\x00')
        device_type = data[64:128].decode('utf-8').rstrip('\x00')
        # device_type_8 = data[128]  # Currently not used
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
        rssi = struct.unpack('h', data[225:227])[0]  # 2 bytes for 'h'
    except (UnicodeDecodeError, struct.error) as e:
        logging.error(f"Error parsing DJI DroneID data: {e}")
        return {}

    # Validate latitude and longitude
    if not is_valid_latlon(drone_lat, drone_lon):
        logging.warning(f"Invalid latitude or longitude received: lat={drone_lat}, lon={drone_lon}")
        return {}

    # Construct drone information dictionary
    drone_info = {
        "id": serial_number,                               # Serial Number as ID
        "callsign": serial_number,                         # Callsign as Serial Number
        "time": iso_timestamp_now(),                       # Current UTC time
        "lat": drone_lat,
        "lon": drone_lon,
        "speed": speed_E,                                  # Assuming speed_E as horizontal speed
        "vspeed": speed_U,                                 # Vertical speed
        "alt": altitude,                                   # Altitude
        "height": height,                                  # Height above ground
        "description": device_type if device_type else "DJI Drone",  # Device Type or default
        "rssi": rssi                                       # RSSI value
    }

    return drone_info

def listen_to_antsdr(ip: str, port: int, drones: dict):
    """
    Connects to AntSDR, receives data, parses it, and updates the drones dictionary.
    """
    while True:
        try:
            logging.info(f"Connecting to AntSDR at {ip}:{port}...")
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.connect((ip, port))
                logging.info(f"Successfully connected to AntSDR at {ip}:{port}")
                sock.settimeout(10.0)  # Timeout for socket operations

                buffer = b''
                while True:
                    try:
                        data = sock.recv(4096)
                        if not data:
                            logging.warning("AntSDR connection closed by the server.")
                            break
                        buffer += data

                        # Assuming each frame is terminated by a newline character
                        while b'\n' in buffer:
                            frame, buffer = buffer.split(b'\n', 1)
                            if frame:
                                drone_info = parse_dji_data(frame)
                                if drone_info:
                                    serial = drone_info["id"]
                                    drones[serial] = drone_info
                                    logging.debug(f"Updated drone: {serial}")
                    except socket.timeout:
                        logging.warning("Socket timeout. No data received.")
                        continue
                    except Exception as e:
                        logging.error(f"Error receiving data: {e}")
                        break  # Exit to reconnect
        except (ConnectionRefusedError, socket.timeout, socket.error) as e:
            logging.error(f"Connection error: {e}. Retrying in {RECONNECT_DELAY} seconds...")
            time.sleep(RECONNECT_DELAY)
        except Exception as e:
            logging.exception(f"Unexpected error: {e}. Retrying in {RECONNECT_DELAY} seconds...")
            time.sleep(RECONNECT_DELAY)

def main():
    """
    Main function to initialize drone data collection and JSON writing.
    """
    # Dictionary to store active drones, keyed by serial number
    drones = {}

    # Start AntSDR listener in a separate thread
    listener_thread = threading.Thread(
        target=listen_to_antsdr,
        args=(ANTSDR_IP, ANTSDR_PORT, drones),
        daemon=True
    )
    listener_thread.start()
    logging.info("Started AntSDR listener thread.")

    # Main loop to periodically write drones data to JSON
    try:
        while True:
            # Convert drones dictionary to a list
            drones_list = list(drones.values())
            write_atomic(JSON_FILE_PATH, drones_list)
            logging.debug(f"Wrote {len(drones_list)} drones to JSON.")
            time.sleep(WRITE_INTERVAL)
    except KeyboardInterrupt:
        logging.info("Script interrupted by user. Exiting...")
        sys.exit(0)
    except Exception as e:
        logging.exception(f"Unexpected error in main loop: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
