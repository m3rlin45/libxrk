"""Test file with intentional type errors to verify type checking works."""

from libxrk.data import AIMXRK


def test_wrong_types() -> None:
    """This should produce type errors."""
    # Error: wrong type for filename (should be str, not int)
    log = AIMXRK(123, progress=None)
    
    # Error: wrong type for progress callback
    log2 = AIMXRK("file.xrk", progress="not a function")
    
    # This should work fine
    log3 = AIMXRK("file.xrk", progress=None)
    
    # Error: trying to call wrong method
    result = log3.nonexistent_method()


if __name__ == "__main__":
    print("This file has intentional type errors for testing")
