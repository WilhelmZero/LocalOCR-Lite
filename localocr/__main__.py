import sys

if "--worker" in sys.argv:
    from .worker import main
else:
    from .app import main

main()
