.PHONY: up down test collect demo
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
