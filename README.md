# python-2-parser

## Test
docker-sync will update the source code into the container running in dev mode.
The files (including run.py) will be located at /usr/app/src.
```shell
# create a shell into the python_2_parser container
cd repo/development
. tools/shortcuts.bash
_sh python-2-parser

# you are now inside the python_2_container:
cd /usr/app/src
. /venv/bin/activate
python -m unittest tests.server.AsyncTest
python -m unittest tests.reference_collector
```
