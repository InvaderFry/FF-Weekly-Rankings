"""Enable ``python -m ff_startsit`` as a PATH-independent way to run the CLI."""

from .cli import main

raise SystemExit(main())
