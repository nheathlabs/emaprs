# Leaflet

Leaflet 1.9.4 (<https://leafletjs.com/>), bundled so the page works when the
browser has no internet access. These files are the npm package's `dist/`
files and `LICENSE`, copied unchanged from
<https://registry.npmjs.org/leaflet/-/leaflet-1.9.4.tgz>
(integrity `sha512-nxS1ynzJOmOlHp+iL3FyWqK89GtNL8U8rvlMOsQdTTssxZwCXh8N2NB3GDQOL+YR3XnWyZAxwQixURb+FA74PA==`).

- `leaflet.js` (`sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=`)
- `leaflet.css` (`sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=`)
- `images/`: the control and default-marker images `leaflet.css` refers to

The source map (`leaflet.js.map`) is left out to keep the package small.

Leaflet is BSD-2-Clause, not GPL; see `LICENSE`.

To upgrade, download the new tarball, check its integrity against
`https://registry.npmjs.org/leaflet/<version>`, replace these files with its
`dist/leaflet.js`, `dist/leaflet.css`, `dist/images/` and `LICENSE`, and
update the version and hashes here.
