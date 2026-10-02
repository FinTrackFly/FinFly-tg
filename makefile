.PHONY: all prep test lint format run run_prod

OK = echo -e '\033[1;32m >>> OK\033[0m'

#
UV_RUN = uv run
#
ruff = ${UV_RUN} --only-group lint ruff
pytest = ${UV_RUN} --only-group test pytest
app = ${UV_RUN} -m finfly

all: test lint run_prod

test:
	${pytest}

lint:
	${ruff} check
	@${OK}

format:
	${ruff} format
	${ruff} check --fix
	@${OK}

run_prod:
	${UV_RUN} --no-dev finfly

run:
	${app}
