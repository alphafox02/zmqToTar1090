# zmqToTar1090

zmqToTar1090 proxies traffic from a Sniffle receiver (https://github.com/alphafox02/Sniffle) to JSON data formatted for ingestion into tar1090. A lot of this project was borrowed from alphafox (cemaexecutor)

## Features

## Requirements
- Sniffle compatible dongle
- tar1090 with local mountpoint to /run/readsb (use docker - https://github.com/sdr-enthusiasts/docker-tar1090)
- python3

### Start the zmqToTar1090 proxy with the expected ZMQ server/port information.

The following command specifies the ZMQ host and ZMQ port that are default

```sh
sudo python3 zmqToTar1090.py
```

This will create a `drone.json` file in the /run/readsb directory for ingestion into tar1090 The path will be parameterized soon.

You can use ```python3 zmqToTar10909.py --help``` to show runtime arguments

## Testing

I created a separate script you can run if you don't yet have a Sniffle compatible dongle. You'll still need tar1090 and the zmqToTar1090 script.

For more information, see the README in that folder

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
