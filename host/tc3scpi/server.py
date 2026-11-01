"""Transports (TCP, stdio) and the command line of tc3_scpi.py."""
import argparse
import socketserver
import sys

from .device import DemoDevice, Device
from .interpreter import Scpi


def run_stdio(scpi):
    for line in sys.stdin:
        sys.stdout.buffer.write(scpi.handle(line))
        sys.stdout.buffer.flush()


def run_tcp(scpi, host, port):
    class H(socketserver.StreamRequestHandler):
        def handle(self):
            for raw in self.rfile:
                try:
                    reply = scpi.handle(raw.decode("ascii", "replace"))
                    if scpi.trace:
                        shown = reply if len(reply) < 60 else reply[:14] + b"...(%d bytes)" % len(reply)
                        print(f"<< {raw.strip().decode('ascii', 'replace'):40s} >> {shown!r}", file=sys.stderr, flush=True)
                    if reply:
                        self.wfile.write(reply)
                except (BrokenPipeError, ConnectionResetError):
                    return

    class S(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with S((host, port), H) as srv:
        print(f"DSO-TC3 SCPI server on {host}:{port} (Ctrl-C to stop)", flush=True)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass


def main(doc="", argv=None):
    ap = argparse.ArgumentParser(description=doc, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-p", "--port", help="Serial port (auto-detects 19f5:5740)")
    ap.add_argument("--host", default="127.0.0.1", help="Listen address (default: loopback only)")
    ap.add_argument("--port-tcp", type=int, default=5025)
    ap.add_argument("--stdio", action="store_true", help="Read commands from stdin instead of TCP")
    ap.add_argument("--rigol", action="store_true", help="Emulate a Rigol DS1052E for PulseView / sigrok-cli (rigol-ds driver)")
    ap.add_argument("--trace", action="store_true", help="Log every command and reply size to stderr")
    ap.add_argument("--demo", action="store_true", help="Use a synthetic device instead of the hardware")
    args = ap.parse_args(argv)
    scpi = Scpi(DemoDevice() if args.demo else Device(args.port), rigol=args.rigol, trace=args.trace)
    if args.stdio:
        run_stdio(scpi)
    else:
        run_tcp(scpi, args.host, args.port_tcp)
