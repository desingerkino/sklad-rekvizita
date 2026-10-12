# -*- coding: utf-8 -*-
"""
Сборка крошечной ONNX-модели без библиотеки onnx.

Нужна в двух местах. Программе — чтобы проверить, запускается ли движок
нейросети на этом компьютере, до того как доверить ему настоящую модель.
Тестам — чтобы прогнать путь «снимок → предобработка → onnxruntime → вектор»
на живом движке, а не на заглушке. Модель принимает тензор 1×3×224×224 и отдаёт вектор из 48 чисел:
средние по 16 клеткам изображения для каждого канала. Этого достаточно, чтобы
похожие картинки давали близкие векторы, а разные — далёкие.

Файл ONNX — это protobuf, поэтому здесь минимальный кодировщик protobuf.
"""
from __future__ import annotations

import struct
from pathlib import Path


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _tag(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _bytes_field(field: int, payload: bytes) -> bytes:
    return _tag(field, 2) + _varint(len(payload)) + payload


def _str_field(field: int, text: str) -> bytes:
    return _bytes_field(field, text.encode("utf-8"))


def _int_field(field: int, value: int) -> bytes:
    return _tag(field, 0) + _varint(value)


def _dim(value: int) -> bytes:
    return _bytes_field(1, _int_field(1, value))            # TensorShapeProto.Dimension


def _tensor_type(elem_type: int, shape: list[int]) -> bytes:
    dims = b"".join(_dim(v) for v in shape)
    tensor = _int_field(1, elem_type) + _bytes_field(2, dims)
    return _bytes_field(1, tensor)                          # TypeProto.tensor_type


def _value_info(name: str, shape: list[int]) -> bytes:
    return _str_field(1, name) + _bytes_field(2, _tensor_type(1, shape))  # 1 = FLOAT


def _node(op_type: str, inputs: list[str], outputs: list[str],
          name: str, attributes: bytes = b"") -> bytes:
    body = b"".join(_str_field(1, i) for i in inputs)
    body += b"".join(_str_field(2, o) for o in outputs)
    body += _str_field(3, name) + _str_field(4, op_type) + attributes
    return body


def _attr_ints(name: str, values: list[int]) -> bytes:
    body = _str_field(1, name) + _int_field(20, 7)          # type = INTS
    body += b"".join(_tag(8, 0) + _varint(v) for v in values)
    return _bytes_field(5, body)                            # NodeProto.attribute


def _attr_int(name: str, value: int) -> bytes:
    body = _str_field(1, name) + _int_field(2, value) + _int_field(20, 2)   # type = INT
    return _bytes_field(5, body)


def model_bytes(side: int = 224, pad_bytes: int = 0) -> bytes:
    """Та же модель, но байтами — без записи на диск."""
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        return build(Path(folder) / "tiny.onnx", side, pad_bytes).read_bytes()


def build(target: Path, side: int = 224, pad_bytes: int = 0) -> Path:
    """
    Строит модель: AveragePool с окном side/4 → выход 1×3×4×4 → Flatten 1×48.

    pad_bytes добавляет в модель описание (doc_string) нужной длины — чтобы
    файл весил как настоящая модель. Программа отбрасывает подозрительно
    маленькие файлы как «не модель, а страницу ошибки», и для проверки
    встроенной модели нужен файл правдоподобного размера.
    """
    window = side // 4
    pool = _node("AveragePool", ["pixel_values"], ["pooled"], "pool",
                 _attr_ints("kernel_shape", [window, window])
                 + _attr_ints("strides", [window, window]))
    flatten = _node("Flatten", ["pooled"], ["image_embeds"], "flatten",
                    _attr_int("axis", 1))

    graph = b"".join([
        _bytes_field(1, pool),
        _bytes_field(1, flatten),
        _str_field(2, "tiny_vision"),
        _bytes_field(11, _value_info("pixel_values", [1, 3, side, side])),
        _bytes_field(12, _value_info("image_embeds", [1, 48])),
    ])

    opset = _str_field(1, "") + _int_field(2, 13)
    model = (_int_field(1, 7)                    # ir_version
             + _str_field(2, "sklad-tests")
             + _bytes_field(7, graph)
             + _bytes_field(8, opset))
    if pad_bytes:
        model += _str_field(6, "x" * pad_bytes)   # ModelProto.doc_string

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(model)
    return target
