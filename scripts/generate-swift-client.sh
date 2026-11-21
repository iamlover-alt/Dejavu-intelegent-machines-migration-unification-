#!/bin/sh
# Compile the Swift client that swift-openapi-generator builds from
# openapi/midplane-v0.yaml. The OpenAPI file is the source of truth; the
# Swift package sees it through a symlink.
set -eu
cd "$(dirname "$0")/.."
link="clients/swift/Sources/MidplaneGateway/openapi.yaml"
if [ ! -e "$link" ]; then
  ln -s ../../../../openapi/midplane-v0.yaml "$link"
fi
swift build --package-path clients/swift
