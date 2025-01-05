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
    log_level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            logging.FileHandler("dji_drone.log"),
            logging.StreamHandler(sys.stdout) if debug else logging.NullHandler()
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

def parse_frame(frame: bytes) -> tuple:
    """
    Parse the incoming frame from AntSDR.

    Returns:
        package_type (int): The type of the package.
        data (bytes): The data payload of the package.
    """
    if len(frame) < 5:
        logging.error(f"Frame too short: {len(frame)} bytes")
        return None, None

    frame_header = frame[:2]
    package_type = frame[2]
    length_bytes = frame[3:5]
    package_length = struct.unpack('H', length_bytes)[0]
    logging.debug(f"Parsed Frame Header: {frame_header}")
    logging.debug(f"Package Type: {package_type}")
    logging.debug(f"Package Length: {package_length}")

    # Ensure the frame has the expected length
    expected_total_length = package_length
    if len(frame) < expected_total_length:
        logging.error(f"Incomplete frame received. Expected {expected_total_length} bytes, got {len(frame)} bytes.")
        return None, None

    data = frame[5:expected_total_length]
    return package_type, data

def parse_dji_data(data: bytes) -> tuple:
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
        if len(data) < EXPECTED_FRAME_SIZE:
            logging.error(f"Received data length {len(data)} is less than expected {EXPECTED_FRAME_SIZE}.")
            return {}, None

        serial_number = data[0:64].decode('utf-8').rstrip('\x00')
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
        rssi = struct.unpack('h', data[225:227])[0]  # Corrected to 2 bytes
    except (UnicodeDecodeError, struct.error) as e:
        logging.error(f"Error parsing DJI DroneID data: {e}")
        # Assign default or placeholder values in case of error
        serial_number = "Unknown"
        device_type = "Got a DJI drone with encryption"
        device_type_8 = 255
        app_lat = app_lon = drone_lat = drone_lon = height = altitude = home_lat = home_lon = freq = speed_E = speed_N = speed_U = rssi = 0

    # Validate drone latitude and longitude
    if not is_valid_latlon(drone_lat, drone_lon):
        logging.warning(f"Invalid drone latitude or longitude received: lat={drone_lat}, lon={drone_lon}")
        return {}, None

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

    # Construct pilot information dictionary if pilot data is valid
    if is_valid_latlon(app_lat, app_lon):
        pilot_id = f"pilot-{serial_number}"              # Unique ID for the pilot
        pilot_info = {
            "id": pilot_id,                                # Unique Pilot ID
            "callsign": pilot_id,                          # Pilot Callsign
            "time": iso_timestamp_now(),                   # Current UTC time
            "lat": app_lat,
            "lon": app_lon,
            "speed": 0,                                     # Pilots might not have speed data
            "vspeed": 0,                                    # Pilots might not have vspeed data
            "alt": altitude,                                # Same altitude as drone
            "height": height,                               # Same height as drone
            "description": "Pilot",                         # Description for pilot
            "rssi": rssi                                    # RSSI value (optional)
        }
        return drone_info, pilot_info
    else:
        return drone_info, None

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
                                    parsed_data, pilot_info = parse_dji_data(frame_data)
                                    if parsed_data:
                                        drones[parsed_data["id"]] = parsed_data
                                        logging.debug(f"Updated drone: {parsed_data['id']}")

                                        if pilot_info:
                                            pilots[pilot_info["id"]] = pilot_info
                                            logging.debug(f"Updated pilot: {pilot_info['id']}")
                                        else:
                                            # Remove pilot entry if no pilot data
                                            pilot_id = f"pilot-{parsed_data['id']}"
                                            if pilot_id in pilots:
                                                del pilots[pilot_id]
                                                logging.debug(f"Removed pilot: {pilot_id}")
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
    logging.info("Started AntSDR listener thread.")

    # Main loop to periodically write drones and pilots data to JSON
    try:
        while True:
            # Combine drones and pilots into a single list
            combined_data = list(drones.values()) + list(pilots.values())

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
