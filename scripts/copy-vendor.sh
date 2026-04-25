#!/usr/bin/env bash
# Copy marked + katex distribution files from node_modules into the renderer
# vendor directory so they can be loaded under the renderer's strict CSP
# (script-src 'self'). Run automatically via `npm run postinstall`.
set -euo pipefail

DEST="src/renderer/vendor"
mkdir -p "$DEST/katex/fonts" "$DEST/katex/contrib"

# marked
cp node_modules/marked/marked.min.js "$DEST/marked.min.js"

# katex core
cp node_modules/katex/dist/katex.min.js  "$DEST/katex/katex.min.js"
cp node_modules/katex/dist/katex.min.css "$DEST/katex/katex.min.css"

# katex auto-render extension (renders math in DOM nodes after the fact)
cp node_modules/katex/dist/contrib/auto-render.min.js "$DEST/katex/contrib/auto-render.min.js"

# katex fonts (referenced by relative URL from katex.min.css -> fonts/...)
cp node_modules/katex/dist/fonts/*.woff2 "$DEST/katex/fonts/"

echo "Vendored into $DEST"
