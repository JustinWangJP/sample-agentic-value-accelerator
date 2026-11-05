#!/bin/sh

export npm_config_cache="${TMPDIR:-/tmp}/registry-portal-npm-cache"
export BLACK_CACHE_DIR="${TMPDIR:-/tmp}/registry-portal-black-cache"
export PRE_COMMIT_HOME="${TMPDIR:-/tmp}/registry-portal-pre-commit"
mkdir -p "$npm_config_cache" "$BLACK_CACHE_DIR" "$PRE_COMMIT_HOME"
