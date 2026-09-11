from pathlib import Path

p = Path("index.html")
s = p.read_text(encoding="utf-8")

# Common mojibake replacements
replacements = {
    "Γé╣": "₹",
    "≡ƒÅ¬": "🛍️",
    "≡ƒ¢ì∩╕Å": "🛒",
    "≡ƒÆ¼": "📲",

    "αñ▓αñ╛αñçαñ╡": "लाइव",
    "αñ╕αÑìαñƒαÑëαñò": "स्टॉक",

    "αñëαñ¬αñ▓αñ¼αÑìαñº": "उपलब्ध",
    "αñ╕αÑìαñƒαÑëαñò": "स्टॉक",

    "αñ«αñ╛αññαÑìαñ░αñ╛": "मात्रा",
    "αñ¿αñ╛αñ«": "नाम",
    "αñ«αÑïαñ¼αñ╛αñçαñ▓": "मोबाइल",
    "αñ¿αñéαñ¼αñ░": "नंबर",
    "αñ¬αÑéαñ░αñ╛": "पूरा",
    "αñíαñ┐αñ▓αÑÇαñ╡αñ░αÑÇ": "डिलीवरी",
    "αñàαñ╡αñ╢αÑìαñ»": "आवश्यक",
    "αñòαÑéαñ¬αñ¿": "कूपन",
    "αñòαÑïαñí": "कोड",
    "αñ»αñªαñ┐": "यदि",
    "αñ╣αÑï": "हो",
    "αññαÑï": "तो",
    "αñªαñ░αÑìαñ£": "डिस्काउंट",
    "αñòαñ░αÑçαñéαñé": "करें",
    "αñòαÑüαñ▓": "कुल",
    "αñ░αñ╛αñ╢αñ┐": "राशि",
    "αñ¡αÑüαñùαññαñ╛αñ¿": "भुगतान",
    "αññαñ░αÑÇαñòαñ╛": "तरीका",
}

for old, new in replacements.items():
    s = s.replace(old, new)

# Fix remaining common mojibake patterns where possible
try:
    for _ in range(2):
        fixed = s.encode("latin1").decode("utf-8")
        if fixed == s:
            break
        s = fixed
except (UnicodeEncodeError, UnicodeDecodeError):
    pass

p.write_text(s, encoding="utf-8")
print("Encoding repair completed.")