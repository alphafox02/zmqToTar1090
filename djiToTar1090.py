#!/usr/bin/env python3
"""
DjiToTar1090.py

Author: CemaXecuter
Description: Connects to AntSDR to receive DJI DroneID data, parses it,
             and writes it to /run/readsb/dji_drone.json in a format compatible with tar1090.
             Also plots pilot information if available.

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

# Configuration Defaults
DEFAULT_ANTSDR_IP = "192.168.1.10"            # Default AntSDR IP
DEFAULT_ANTSDR_PORT = 41030                   # Default AntSDR Port
JSON_FILE_PATH = "/run/readsb/dji_drone.json" # Output JSON file
RECONNECT_DELAY = 5                            # Seconds to wait before reconnecting
WRITE_INTERVAL = 1                             # Seconds between JSON writes
EXPECTED_FRAME_SIZE = 227                      # Expected bytes per frame

def setup_logging(debug: bool):
    """Configure logging based on debug flag."""
    if debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(asctime)s [%(levelname)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
            handlers=[
                logging.FileHandler("dji_drone.log"),
                logging.StreamHandler(sys.stdout)
            ]
        )
    else:
        logging.basicConfig(
            level=logging.WARNING,  # Only warnings and errors will be logged
            format='%(asctime)s [%(levelname)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
            handlers=[
                logging.FileHandler("dji_drone.log")
            ]
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

def parse_frame(frame):
    frame_header = frame[:2]
    package_type = frame[2]
    length_bytes = frame[3:5]
    # struct.unpack parse uint16_t
    package_length = struct.unpack('H', length_bytes)[0]
    print(f"package_length: {package_length}")
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
    except (UnicodeDecodeError, struct.error) as e:
        print(f"Error parsing DJI DroneID data: {e}")
        # Assign default or placeholder values in case of error
        serial_number = "Unknown"
        device_type = "Got a DJI drone with encryption"
        device_type_8 = 255
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

def listen_to_antsdr(ip: str, port: int, drones: dict, pilots: dict, debug: bool):
    """
    Connects to AntSDR, receives data, parses it, and updates the drones and pilots dictionaries.
    """
    while True:
        try:
            logging.info(f"Connecting to AntSDR at {ip}:{port}...")
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.connect((ip, port))
                logging.info(f"Successfully connected to AntSDR at {ip}:{port}")
                # Removed socket timeout to prevent premature disconnections

                buffer = b''
                while True:
                    try:
                        data = sock.recv(1024)
                        if not data:
                            logging.warning("AntSDR connection closed by the server.")
                            break
                        buffer += data

                        # Log raw data if in debug mode
                        if debug:
                            logging.debug(f"Raw data received ({len(data)} bytes): {data.hex()}")

                        # Process data in fixed-length frames
                        while len(buffer) >= EXPECTED_FRAME_SIZE:
                            frame = buffer[:EXPECTED_FRAME_SIZE]
                            buffer = buffer[EXPECTED_FRAME_SIZE:]
                            if frame:
                                package_type, frame_data = parse_frame(frame)
                                if package_type == 0x01 and frame_data:
                                    parsed_data = parse_data_1(frame_data)
                                    if parsed_data:
                                        drones[parsed_data["serial_number"]] = parsed_data
                                        logging.debug(f"Updated drone: {parsed_data['serial_number']}")
                                        
                                        # If pilot data is present and applicable, handle it here
                                        # For example, associate pilot data based on certain conditions
                                        # Currently, no pilot data handling is implemented
                    except Exception as e_inner:
                        logging.error(f"Error receiving data: {e_inner}")
                        break  # Exit the inner loop to reconnect
        except (ConnectionRefusedError, socket.error) as e:
            logging.error(f"Connection error: {e}. Retrying in {RECONNECT_DELAY} seconds...")
            time.sleep(RECONNECT_DELAY)
        except Exception as e:
            logging.exception(f"Unexpected error: {e}. Retrying in {RECONNECT_DELAY} seconds...")
            time.sleep(RECONNECT_DELAY)

def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Connect to AntSDR, parse DJI DroneID data, and output to tar1090-compatible JSON.")
    parser.add_argument('-d', '--debug', action='store_true',
                        help='Enable debug mode for verbose output and raw data logging.')
    parser.add_argument('-i', '--ip', type=str, default=DEFAULT_ANTSDR_IP,
                        help=f'Specify the AntSDR IP address. Default is {DEFAULT_ANTSDR_IP}.')
    parser.add_argument('-p', '--port', type=int, default=DEFAULT_ANTSDR_PORT,
                        help=f'Specify the AntSDR port. Default is {DEFAULT_ANTSDR_PORT}.')
    return parser.parse_args()

def handle_shutdown(signum, frame):
    """Handle shutdown signals for graceful exit."""
    print("Shutdown signal received. Exiting gracefully...")
    sys.exit(0)

def main():
    """
    Main function to initialize drone data collection and JSON writing.
    """
    args = parse_args()
    setup_logging(args.debug)

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, handle_shutdown)   # Handle Ctrl+C
    signal.signal(signal.SIGTERM, handle_shutdown)  # Handle termination signals

    # Dictionaries to store active drones and pilots, keyed by their unique IDs
    drones = {}
    pilots = {}

    # Start AntSDR listener in a separate thread
    listener_thread = threading.Thread(
        target=listen_to_antsdr,
        args=(args.ip, args.port, drones, pilots, args.debug),
        daemon=True
    )
    listener_thread.start()
    print("Started AntSDR listener thread.")

    # Main loop to periodically write drones and pilots data to JSON
    try:
        while True:
            # Combine drones and pilots into a single list
            combined_data = list(drones.values()) + list(pilots.values())

            # Write the combined data to JSON atomically
            write_atomic(JSON_FILE_PATH, combined_data)
            if args.debug:
                print(f"Wrote {len(drones)} drones and {len(pilots)} pilots to JSON.")
            time.sleep(WRITE_INTERVAL)
    except KeyboardInterrupt:
        print("Script interrupted by user. Exiting...")
        sys.exit(0)
    except Exception as e:
        logging.exception(f"Unexpected error in main loop: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
