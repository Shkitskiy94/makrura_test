IMAGE_NAME ?= ctchub/makrura
SERVICE_NAME ?= makrura
ADDITIONAL_DOCKER_BUILD_ARGS ?=
BASE_GITLAB_URL ?= "git+https://gitlab.ctcmedia.ru"

export IMAGE_NAME

# Version is a git tag name for current SHA commit hash or this hash if tag is not presented
APP_VERSION ?= $$(git describe --exact-match --tags $(git log -n1 --pretty='%h') 2> /dev/null || \
                  git log -n1 --pretty='commit_sha:%h')

vars:
	@echo IMAGE_NAME=${IMAGE_NAME}
	@echo BASE_GITLAB_URL=${BASE_GITLAB_URL}
	@echo ADDITIONAL_DOCKER_BUILD_ARGS=${ADDITIONAL_DOCKER_BUILD_ARGS}
	@echo APP_VERSION=${APP_VERSION}

image:
	docker build -f build/Dockerfile -t ${IMAGE_NAME} . --force-rm=True \
		--build-arg BASE_GITLAB_URL=${BASE_GITLAB_URL} \
		--build-arg APP_VERSION=${APP_VERSION} \
		--build-arg LAB_PRIVATE_TOKEN=${LAB_PRIVATE_TOKEN} \
		${ADDITIONAL_DOCKER_BUILD_ARGS}

run:
	docker-compose -f ./build/docker-compose.yml up ${SERVICE_NAME}

push_to_registry: image
	docker push ${IMAGE_NAME}

# --- Тесты ---

clean_test:
	docker-compose -f ./build/docker-compose.test.yml down
	docker-compose -f ./build/docker-compose.test.yml rm -s -v -f

test: image clean_test
	docker-compose -f ./build/docker-compose.test.yml up --abort-on-container-exit test

ruff_lint: image clean_test
	docker-compose -f ./build/docker-compose.test.yml up --abort-on-container-exit ruff_lint

mypy_lint: image clean_test
	docker-compose -f ./build/docker-compose.test.yml up --abort-on-container-exit mypy_lint

get_test_report .SILENT:
	docker cp $$(docker-compose -f ./build/docker-compose.test.yml ps -q test):/tmp/test_report/junit_report.xml ./junit_report.xml || true
	cat ./junit_report.xml

clean:
	docker-compose -f ./build/docker-compose.yml down
	docker-compose -f ./build/docker-compose.test.yml down
	docker-compose -f ./build/docker-compose.test.yml rm -s -v -f
	docker-compose -f ./build/docker-compose.yml rm -s -v -f