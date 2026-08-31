import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from web_server import get_basket, save_basket, BasketSaveRequest, BasketItem

def test_basket_crud():
    print("Testing get_basket()...")
    res = get_basket()
    print(f"Initial get_basket: {res}")
    assert "portfolio" in res, "Should contain portfolio key"
    assert "unallocated_cash_eur" in res, "Should contain cash key"

    print("Testing save_basket()...")
    test_req = BasketSaveRequest(
        portfolio=[BasketItem(ticker="UMAC", shares=100.0, wac=15.5)],
        unallocated_cash_eur=5000.0,
        unallocated_cash_usd=5400.0
    )
    save_res = save_basket(test_req)
    print(f"save_basket result: {save_res}")
    assert save_res.get("status") == "success"

    print("Verifying updated get_basket()...")
    updated = get_basket()
    print(f"Updated get_basket: {updated}")
    assert len(updated["portfolio"]) == 1
    assert updated["portfolio"][0]["ticker"] == "UMAC"
    assert updated["portfolio"][0]["shares"] == 100.0
    assert updated["portfolio"][0]["wac"] == 15.5
    assert updated["unallocated_cash_eur"] == 5000.0

    print("ALL BASKET CRUD TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    test_basket_crud()
