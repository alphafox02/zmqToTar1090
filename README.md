# zmqToTar1090

zmqToTar1090 proxies traffic from a Sniffle receiver (https://github.com/alphafox02/Sniffle) to JSON data formatted for ingestion into tar1090. A lot of this project was borrowed from alphafox (cemaexecutor)

## Features

## Requirements
- Sniffle compatible dongle
- tar1090 with local mountpoint to /run/readsb (use docker - https://github.com/sdr-enthusiasts/docker-tar1090)
- python3

## Setup and Usage

### Setup tar1090 for drone ingestion

#### Dockerfile

- Append `TAR1090_CONFIGJS_APPEND='droneJson="./data/drone.json"'` to the ENV section of the docker-tar1090 Dockerfile

#### Docker Compose

- Append `TAR1090_CONFIGJS_APPEND='droneJson="./data/drone.json"'` to the "environment" section of the docker-compose-tar1090.yml file

### Clone Sniffle from alphafox02 repo (this it the one I used, you can likely use the one he forked from kerler)

```sh
git clone https://github.com/alphafox02/Sniffle
cd Sniffle
```

### Run sniffle providing ZMQ output
```sh
python3 Sniffle/python_cli/sniff_receiver -l -e -z --zmqhost 127.0.0.1 --zmqport 2402
```

This command configures the Sniffle dongle to look for Bluetooth 5 long range extended packets and forwards them via ZeroMQ (ZMQ).

### Start the zmqToTar1090 proxy with the expected ZMQ server/port information.

The following command specifies the ZMQ host and ZMQ port that are default

```sh
python3 zmqToTar1090.py --zmq-host 127.0.0.1 --zmqport 24042
```

This will create a `drone.json` file in the /run/readsb directory for ingestion into tar1090 The path will be parameterized soon.

You can use ```python3 zmqToTar10909.py --help``` to show runtime arguments

## How It Works

## Troubleshooting

## License

```
MIT License

© 2024 l0g

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
