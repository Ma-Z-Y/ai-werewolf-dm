import pydantic

import werewolf_dm


def test_package_uses_pydantic_v2_and_version():
    assert werewolf_dm.__version__ == "0.2.0"
    assert pydantic.VERSION.startswith("2.")
