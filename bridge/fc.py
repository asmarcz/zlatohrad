#!/usr/bin/env python3
"""Send Python to the running FreeCAD through claude_bridge.FCMacro.

    fc.py run script.py      run a file (or '-' for stdin)
    fc.py run -c "code"      run a snippet
    fc.py shot out.png [w h] save the 3D view as an image
    fc.py ping

Inside the code, assign to `result` to get a value back.
"""

import sys
import xmlrpc.client

URL = "http://127.0.0.1:9876"


def main(argv):
    proxy = xmlrpc.client.ServerProxy(URL, allow_none=True)
    cmd, args = argv[0], argv[1:]
    if cmd == "ping":
        print(proxy.ping())
        return 0
    if cmd == "run":
        if args[0] == "-c":
            code = args[1]
        elif args[0] == "-":
            code = sys.stdin.read()
        else:
            code = open(args[0]).read()
        reply = proxy.run(code)
    elif cmd == "shot":
        reply = proxy.screenshot(*args[:1], *map(int, args[1:3]))
    else:
        print(__doc__, file=sys.stderr)
        return 2

    if reply.get("output"):
        print(reply["output"], end="")
    if reply.get("result") is not None:
        print("result:", reply["result"])
    if not reply["ok"]:
        print(reply["error"], file=sys.stderr, end="")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
