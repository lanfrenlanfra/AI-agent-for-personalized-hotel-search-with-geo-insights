.PHONY: up down seed lint test collect demo check-sources collect-extra
up:        ## запустить сервис на :8080
	docker compose up --build
down:
	docker compose down
test:      ## тесты (нужны requirements-dev.txt)
	python -m pytest -q
collect:   ## собрать данные Островка и OSM (нужен интернет), результат — в data/
	docker compose --profile collect run --rm collect
demo:      ## сгенерировать синтетический демо-набор локально
	python scripts/make_demo.py
seed:
	docker compose exec app python -c "from app import pipeline; pipeline.bootstrap(force=True)"
lint:
	ruff check .
check-sources:
	python scripts/check_sources.py
collect-extra:
	python scripts/collect_extra.py
