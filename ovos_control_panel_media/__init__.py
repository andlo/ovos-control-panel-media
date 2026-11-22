"""Media: what the device is playing, for ovos-control-panel.

A panel extension: the panel finds this page through the
``opm.control_panel.page`` entry point (see pyproject.toml and the panel's
docs/pages.md, "Panel extensions"). It talks to the player over the
``ovos.common_play.*`` messages, which ovos-media and the older
ovos-plugin-common-play both answer: what is playing, play and pause, the
queue, shuffle and repeat, seeking, the volume, the other matches for the
request and the liked songs. Nothing here runs outside the panel.

It was one of the panel's own pages until a device that plays no media (a
reception desk, a phone line) no longer needed to carry it.
"""


def routes(r):
    # imported here, so reading PAGE needs no web framework
    from ovos_control_panel_media.api import routes as add
    add(r)


PAGE = {
    "api_version": 1,
    "id": "media",
    "path": "/media",
    "title": {"key": "nav.media", "label": "Media"},
    "group": "library",
    "order": 5,
    "needs": ["ovos-media", "ovos-plugin-common-play", "ovos-ocp-pipeline-plugin"],
    "html": "page.html",
    "icon": "icon.svg",
    "heading": {"key": "hdr.media", "label": "Media"},
    "i18n": "i18n",
    "routes": routes,
}
