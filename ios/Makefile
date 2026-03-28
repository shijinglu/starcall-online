.PHONY: install build run test clean

## install — resolve Swift package dependencies
install:
	swift package resolve

## build — compile the project
build:
	swift build

## run — build and launch the executable
run: build
	swift run VoiceAgent

## test — run unit tests
test:
	swift test

## clean — remove build artifacts
clean:
	swift package clean
	rm -rf .build
