

--- Monetarios + Corto + Felxible 

def infer_fund_nature_generic(text):
    t = text.lower()
    if is_explicit_monetary_fund(t):
        return "Monetario"
    if any(k in t for k in [
        "short duration", "ultra short",
        "court durée", "short term bond",
        "low duration", "floating rate"
    ]):
        return "RF Corto"
    if any(k in t for k in [
        "bond", "renta fija", "fixed income", "debt"
    ]):
        return "Renta Fija"
        
    if any(k in t for k in [
        "balanced", "allocation", "multi-asset",
        "equities and bonds", "acciones y bonos",
        "up to", "% equity",
        "may invest in bonds", "may invest in debt",
        "combination of equities and bonds"
    ]):
        return {"Fund_Nature": "Mixto"}

    # -------------------------------
    # Exclusión a Alternativos
    # -------------------------------
    if any(k in t for k in [
        "absolute return", "retorno absoluto",
        "capital preservation", "hedge"
    ]):
        return {"Fund_Nature": "Alternativo"}

    # -------------------------------
    # Confirmación RV
    # -------------------------------
    if any(k in t for k in [
        "equity", "equities", "shares",
        "stocks", "acciones"
    ]):
        return {"Fund_Nature": "Renta Variable"}        
        
        
        
    return "No determinado"


