#!/bin/sh
# Install the tested Omnigent pin separately from Research Harness.
set -eu

if [ "$#" -ne 1 ]; then
    echo "Usage: sh examples/omnigent/setup.sh /absolute/path/to/omnigent-checkout" >&2
    exit 2
fi

research_omni_source=$1
research_omni_pin=be042b390e293a8d586cbb7e403a2ce0ce38fc62
if [ -e "$research_omni_source" ]; then
    if [ "$(git -C "$research_omni_source" rev-parse HEAD)" != "$research_omni_pin" ]; then
        echo "Existing checkout is not the tested pin; choose a new destination." >&2
        exit 2
    fi
    if [ -n "$(git -C "$research_omni_source" status --porcelain --untracked-files=no)" ]; then
        echo "Existing checkout has tracked changes; choose a new destination." >&2
        exit 2
    fi
else
    git clone --filter=blob:none --no-checkout https://github.com/omnigent-ai/omnigent.git "$research_omni_source"
    git -C "$research_omni_source" checkout --detach "$research_omni_pin"
fi

cd "$research_omni_source"
uvx --from uv==0.11.8 uv sync --frozen --no-dev --python 3.13
