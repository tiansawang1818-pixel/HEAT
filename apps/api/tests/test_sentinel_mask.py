from app.services.connectors.earth_engine import SentinelConnector


class _Band:
    def __init__(self, excluded=()):
        self.excluded = set(excluded)

    def neq(self, value):
        return _Band(self.excluded | {value})

    def And(self, other):
        return _Band(self.excluded | other.excluded)

    def rename(self, _):
        return self

    def updateMask(self, mask):
        self.mask = mask
        return self


class _Image:
    def select(self, band):
        return _Band()

    def normalizedDifference(self, bands):
        return _Band()


class _EE:
    class Reducer:
        @staticmethod
        def mean():
            return "mean"


def test_ndvi_mask_keeps_bright_urban_classes():
    analysis = SentinelConnector()._analysis(_EE, _Image())
    assert analysis["mask"].excluded == {3, 9, 10}
    assert SentinelConnector.processing_recipe["cloud_mask"] == "SCL excludes 3, 9, 10"
