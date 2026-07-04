import pytest

from ir import codes


def test_add_find_len():
    lib = codes.CodeLibrary()
    c = codes.IrCode("A", "nec", "0x1")
    lib.add(c)
    assert lib.find("A") is c and len(lib) == 1
    assert lib.find("missing") is None


def test_duplicate_and_replace():
    lib = codes.CodeLibrary([codes.IrCode("A", "nec", "0x1")])
    with pytest.raises(codes.CodeError):
        lib.add(codes.IrCode("A", "raw", "1 2"))
    lib.add(codes.IrCode("A", "raw", "1 2"), replace=True)
    assert lib.find("A").kind == "raw"


def test_bad_kind_and_empty():
    with pytest.raises(codes.CodeError):
        codes.IrCode("A", "bogus", "x")
    with pytest.raises(codes.CodeError):
        codes.IrCode("", "nec", "0x1")
    with pytest.raises(codes.CodeError):
        codes.IrCode("A", "nec", "")


def test_remove():
    lib = codes.CodeLibrary([codes.IrCode("A", "slot", "3")])
    lib.remove("A")
    assert len(lib) == 0
    with pytest.raises(codes.CodeError):
        lib.remove("A")


def test_dict_roundtrip():
    lib = codes.CodeLibrary([codes.IrCode("A", "nec", "0x1", "hi")])
    lib2 = codes.CodeLibrary.from_dict(lib.to_dict())
    assert lib2.find("A").note == "hi" and lib2.find("A").value == "0x1"


def test_from_dict_validation():
    with pytest.raises(codes.CodeError):
        codes.CodeLibrary.from_dict([])
    with pytest.raises(codes.CodeError):
        codes.CodeLibrary.from_dict({"codes": "nope"})


def test_load_missing_returns_empty(tmp_path):
    assert len(codes.load(str(tmp_path / "nope.json"))) == 0


def test_save_load_roundtrip(tmp_path):
    p = str(tmp_path / "lib.json")
    codes.save(p, codes.CodeLibrary([codes.IrCode("A", "slot", "3")]))
    assert codes.load(p).find("A").value == "3"
