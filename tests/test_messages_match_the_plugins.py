"""What the page sends must be what the plugins read: the volume, as the
fraction both ovos-PHAL-plugin-alsa and -pulseaudio scale by 100."""
import pytest
from ovos_utils.fakebus import FakeBus

from ovos_control_panel_media import player as media

WATCHED = ("mycroft.volume.set", "mycroft.volume.set.gui")


@pytest.fixture
def sent():
    bus = FakeBus()
    seen = []
    for topic in WATCHED:
        bus.on(topic, lambda m: seen.append(m))
    return bus, seen


def _find(seen, msg_type):
    for message in seen:
        if message.msg_type == msg_type:
            return message
    raise AssertionError(f"{msg_type} was never sent; got {[m.msg_type for m in seen]}")


def test_the_media_volume_is_sent_as_the_fraction_both_plugins_expect(sent):
    """Volume is owned by the PHAL plugins, and both scale by 100.

    alsa binds `mycroft.volume.set`; pulseaudio binds that and
    `mycroft.volume.set.gui`. Sending `.gui` with a 0-100 int was ignored by
    alsa and read by pulseaudio as 7300%, clamped to full volume.
    """
    bus, seen = sent
    media.set_volume(bus, 40)

    types = [m.msg_type for m in seen]
    assert "mycroft.volume.set.gui" not in types, (
        "alsa does not bind mycroft.volume.set.gui at all, so use the message "
        "both plugins listen on"
    )
    message = _find(seen, "mycroft.volume.set")
    # both plugins do `message.data["percent"] * 100`
    assert message.data["percent"] == pytest.approx(0.4)





def test_the_volume_body_rejects_a_bool_percent():
    from pydantic import ValidationError
    from ovos_control_panel_media.api import MediaVolumeBody
    assert MediaVolumeBody(percent=50).percent == 50
    with pytest.raises(ValidationError):
        MediaVolumeBody(percent=True)
