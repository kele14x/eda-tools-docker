// A 32-bit adder. The redundant +0 demonstrates IR optimization.
pub fn add(x: u32, y: u32) -> u32 {
    x + y + u32:0
}

#[test]
fn add_small_numbers() {
    assert_eq(add(u32:2, u32:3), u32:5)
}

#[test]
fn add_zero() {
    assert_eq(add(u32:42, u32:0), u32:42)
}

#[test]
fn add_wraps_at_32_bits() {
    assert_eq(add(u32:0xffff_ffff, u32:1), u32:0)
}

#[quickcheck(test_count=100)]
fn add_is_commutative(x: u32, y: u32) -> bool {
    add(x, y) == add(y, x)
}
