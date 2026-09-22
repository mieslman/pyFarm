from app.models.farm import FarmField, PlantTile
from app.models.product import Product


def test_product_model():
    p = Product(pid=17, name="Karotte", price=0.34, size_x=1, size_y=1, amount=120)
    assert p.pid == 17
    assert p.name == "Karotte"
    assert not p.is_multi_tile
    assert p.total_amount == 120


def test_farm_field_model():
    tile = PlantTile(tile_id=1, pid=17, phase=4, remain_seconds=0, is_watered=True)
    field = FarmField(farm_id=1, position=1, name="Acker 1", tiles=[tile])
    assert field.has_ready_crops
    assert not field.needs_watering
    assert not field.is_fully_planted
