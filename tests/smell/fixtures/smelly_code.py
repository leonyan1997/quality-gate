"""
Fixture: code with all detectable bad smells in named functions.
Each function/class is structured so a specific rule triggers on it.
"""

from __future__ import annotations

import math
import os
import sys

# ── Long Method ──────────────────────────────────────────────────────────────


def long_function():
    """A function with many lines to trigger long-method rule."""
    a = 1
    b = 2
    c = 3
    d = 4
    e = 5
    f = 6
    g = 7
    h = 8
    i = 9
    j = 10
    k = 11
    l = 12
    m = 13
    n = 14
    o = 15
    p = 16
    q = 17
    r = 18
    s = 19
    t = 20
    u = 21
    v = 22
    w = 23
    x = 24
    y = 25
    z = 26
    aa = 27
    bb = 28
    cc = 29
    dd = 30
    ee = 31
    ff = 32
    gg = 33
    hh = 34
    ii = 35
    jj = 36
    kk = 37
    ll = 38
    mm = 39
    nn = 40
    oo = 41
    return aa + bb

# ── Large Class ──────────────────────────────────────────────────────────────


class HugeClass:
    """A class with many methods to trigger large-class rule."""

    def method1(self): pass
    def method2(self): pass
    def method3(self): pass
    def method4(self): pass
    def method5(self): pass
    def method6(self): pass
    def method7(self): pass
    def method8(self): pass
    def method9(self): pass
    def method10(self): pass
    def method11(self): pass
    def method12(self): pass

# ── Long Parameter List ──────────────────────────────────────────────────────


def lots_of_params(a, b, c, d, e, f, g):
    """Function with many params to trigger long-parameter-list rule."""
    return a + b + c

# ── Dead Import ──────────────────────────────────────────────────────────────


# sys is imported at the top but never used ✓ (caught by dead-import)
# os is imported at the top and used below ✓

def uses_os():
    return os.name

# ── File-Level Dead Code ─────────────────────────────────────────────────────


def _unused_private_func():
    """Private function never called in this file → dead code."""
    return 42


_variable_unused = "hello"

# ── Switch Statements ────────────────────────────────────────────────────────


def many_branches(value):
    """Match expression with many cases."""
    match value:
        case 1:
            return "a"
        case 2:
            return "b"
        case 3:
            return "c"
        case 4:
            return "d"
        case 5:
            return "e"

# ── Data Class ───────────────────────────────────────────────────────────────


class PlainDataBag:
    """Class with only fields and no behavior → data class candidate."""
    name: str
    age: int
    email: str

# ── Lazy Class ───────────────────────────────────────────────────────────────


class DoNothingClass:
    """Class with only trivial methods → lazy class."""

    def trivial(self):
        pass

    def also_trivial(self):
        """Just a docstring."""
        pass


# ── Pydantic BaseModel (应跳过数据类检测) ─────────────────────────────────

import pydantic
from pydantic import BaseModel


class UserModel(BaseModel):
    """Pydantic v2 BaseModel — 不应被标记为数据类候选"""
    name: str
    email: str
    age: int


class AdminModel(pydantic.BaseModel):
    """Pydantic v2 BaseModel — 用 pydantic.BaseModel 形式"""
    username: str
    role: str
