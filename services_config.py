"""
Lavora Clinic — Service Definitions

Lavora is a multi-speciality aesthetic, dermatology, and regenerative
medicine clinic in Muscat. Service catalogue and doctor list come from
the public clinic brief — see prompts/voice_receptionist.py for the full
brand profile that the receptionist uses.

Each department has:
- capacity: how many clients can be served simultaneously
- doctor / doctors: which specialist(s) handle the department
- sub_services: specific treatments with their duration in minutes

Pricing note: the public brief does not list prices. The OMR figures
below are placeholders chosen to be reasonable for a luxury aesthetic
clinic in Muscat — replace with the clinic's real card before going
live. They are flagged as `_placeholder_price: True` for any tooling
that wants to surface that uncertainty.
"""

SERVICES = {
    "dermatology": {
        "name": "Dermatology & Medical Skin Care",
        "name_ar": "الأمراض الجلدية والعناية الطبية بالبشرة",
        "capacity": 2,
        # Multiple dermatology specialists; caller can choose or we route.
        "doctors": ["Dr. Hussein", "Dr. Neda", "Dr. Amani"],
        "sub_services": {
            "frax_pro": {
                "name": "Frax Pro Laser",
                "name_ar": "ليزر فراكس برو",
                "duration": 45,
                "price_omr": 180,
                "_placeholder_price": True,
            },
            "picoway": {
                "name": "Picoway Laser",
                "name_ar": "ليزر بيكوواي",
                "duration": 45,
                "price_omr": 200,
                "_placeholder_price": True,
            },
            "redtouch": {
                "name": "RedTouch Laser",
                "name_ar": "ليزر ريد تاتش",
                "duration": 30,
                "price_omr": 150,
                "_placeholder_price": True,
            },
            "skin_resurfacing": {
                "name": "Skin Resurfacing",
                "name_ar": "تجديد البشرة",
                "duration": 45,
                "price_omr": 160,
                "_placeholder_price": True,
            },
            "chemical_peel": {
                "name": "Chemical Peel",
                "name_ar": "التقشير الكيميائي",
                "duration": 30,
                "price_omr": 90,
                "_placeholder_price": True,
            },
            "scar_stretch_mark": {
                "name": "Scar & Stretch Mark Treatment",
                "name_ar": "علاج الندبات وعلامات التمدد",
                "duration": 45,
                "price_omr": 140,
                "_placeholder_price": True,
            },
            "vascular_laser": {
                "name": "Vascular Laser",
                "name_ar": "ليزر الأوعية الدموية",
                "duration": 30,
                "price_omr": 130,
                "_placeholder_price": True,
            },
        },
    },
    "aesthetics": {
        "name": "Non-Surgical Aesthetics",
        "name_ar": "التجميل غير الجراحي",
        "capacity": 2,
        "doctors": ["Dr. Soraya", "Dr. Neda", "Dr. Hussein", "Dr. Amani"],
        "sub_services": {
            "botox": {
                "name": "Botox",
                "name_ar": "بوتوكس",
                "duration": 30,
                "price_omr": 150,
                "price_unit": "per area",
                "_placeholder_price": True,
            },
            "dermal_filler": {
                "name": "Dermal Filler",
                "name_ar": "فيلر",
                "duration": 45,
                "price_omr": 180,
                "price_unit": "per syringe",
                "_placeholder_price": True,
            },
            "profhilo": {
                "name": "Profhilo Skinbooster",
                "name_ar": "بروفايلو",
                "duration": 30,
                "price_omr": 220,
                "_placeholder_price": True,
            },
            "polynucleotides": {
                "name": "Polynucleotides Skinbooster",
                "name_ar": "بولينوكليوتيدات",
                "duration": 30,
                "price_omr": 250,
                "_placeholder_price": True,
            },
            "korean_thread_lift": {
                "name": "Korean Thread Lift",
                "name_ar": "شد الوجه بالخيوط الكورية",
                "duration": 60,
                "price_omr": 300,
                "_placeholder_price": True,
            },
            "aptos_thread_lift": {
                "name": "Aptos Thread Lift",
                "name_ar": "شد الوجه بخيوط أبتوس",
                "duration": 75,
                "price_omr": 400,
                "_placeholder_price": True,
            },
            "endolift": {
                "name": "Endolift",
                "name_ar": "إندوليفت",
                "duration": 60,
                "price_omr": 450,
                "_placeholder_price": True,
            },
            "fotona_4d": {
                "name": "Fotona 4D Facial Lifting",
                "name_ar": "فوتونا فور دي لشد الوجه",
                "duration": 60,
                "price_omr": 280,
                "_placeholder_price": True,
            },
        },
    },
    "regenerative": {
        "name": "Regenerative & Cellular Therapies",
        "name_ar": "العلاجات التجديدية والخلوية",
        "capacity": 1,
        "doctor": "Dr. Soraya",  # founder, leads regenerative medicine
        "sub_services": {
            "prp": {
                "name": "PRP (Platelet-Rich Plasma)",
                "name_ar": "بلازما الصفائح الدموية الغنية",
                "duration": 45,
                "price_omr": 180,
                "_placeholder_price": True,
            },
            "mesotherapy": {
                "name": "Mesotherapy",
                "name_ar": "الميزوثيرابي",
                "duration": 45,
                "price_omr": 150,
                "_placeholder_price": True,
            },
            "exosome_therapy": {
                "name": "Exosome Therapy",
                "name_ar": "علاج الإكسوسومات",
                "duration": 60,
                "price_omr": 350,
                "_placeholder_price": True,
            },
            "stem_cell_fat_transfer": {
                "name": "Stem Cell Fat Transfer",
                "name_ar": "نقل الدهون بالخلايا الجذعية",
                "duration": 90,
                "price_omr": 800,
                "_placeholder_price": True,
            },
        },
    },
    "slimming": {
        "name": "Body Slimming",
        "name_ar": "تنحيف الجسم",
        "capacity": 2,
        "doctor": None,  # device-based; performed by trained technician
        "sub_services": {
            "onda_plus": {
                "name": "Onda Plus",
                "name_ar": "أوندا بلس",
                "duration": 60,
                "price_omr": 90,
                "_placeholder_price": True,
            },
            "redustim": {
                "name": "Redustim",
                "name_ar": "ريدوستيم",
                "duration": 45,
                "price_omr": 70,
                "_placeholder_price": True,
            },
            "body_wrap": {
                "name": "Body Wrap",
                "name_ar": "لفائف الجسم",
                "duration": 60,
                "price_omr": 50,
                "_placeholder_price": True,
            },
        },
    },
    "gynecology": {
        "name": "Aesthetic Gynecology",
        "name_ar": "أمراض النساء التجميلية",
        "capacity": 1,
        "doctor": "Dr. Leila",  # MD, OB/GYN specialist
        "sub_services": {
            "vaginal_rejuvenation": {
                "name": "Vaginal Rejuvenation",
                "name_ar": "تجديد المهبل",
                "duration": 45,
                "price_omr": 250,
                "_placeholder_price": True,
            },
            "pelvic_floor": {
                "name": "Pelvic Floor Strengthening",
                "name_ar": "تقوية قاع الحوض",
                "duration": 30,
                "price_omr": 120,
                "_placeholder_price": True,
            },
            "intimate_rejuvenation": {
                "name": "Non-Surgical Intimate Rejuvenation",
                "name_ar": "تجديد المنطقة الحساسة دون جراحة",
                "duration": 45,
                "price_omr": 200,
                "_placeholder_price": True,
            },
            "vaginoplasty": {
                "name": "Vaginoplasty (Surgical)",
                "name_ar": "تجميل المهبل (جراحي)",
                "duration": 120,
                "price_omr": 1500,
                "_placeholder_price": True,
            },
            "labiaplasty": {
                "name": "Labiaplasty (Surgical)",
                "name_ar": "تجميل الشفرين (جراحي)",
                "duration": 90,
                "price_omr": 1200,
                "_placeholder_price": True,
            },
        },
    },
    "laser_hair_removal": {
        "name": "Laser Hair Removal",
        "name_ar": "إزالة الشعر بالليزر",
        "capacity": 4,
        "doctor": None,  # technician-only
        "sub_services": {
            "bikini": {
                "name": "Bikini Laser",
                "name_ar": "ليزر البكيني",
                "duration": 15,
                "price_omr": 25,
                "_placeholder_price": True,
            },
            "underarms": {
                "name": "Underarms Laser",
                "name_ar": "ليزر تحت الإبط",
                "duration": 15,
                "price_omr": 20,
                "_placeholder_price": True,
            },
            "face": {
                "name": "Face Laser",
                "name_ar": "ليزر الوجه",
                "duration": 20,
                "price_omr": 30,
                "_placeholder_price": True,
            },
            "legs": {
                "name": "Legs Laser",
                "name_ar": "ليزر الساقين",
                "duration": 30,
                "price_omr": 50,
                "_placeholder_price": True,
            },
            "arms": {
                "name": "Arms Laser",
                "name_ar": "ليزر الذراعين",
                "duration": 25,
                "price_omr": 40,
                "_placeholder_price": True,
            },
            "full_body_women": {
                "name": "Full Body Laser (Women)",
                "name_ar": "ليزر كامل الجسم للسيدات",
                "duration": 60,
                "price_omr": 120,
                "_placeholder_price": True,
            },
            "full_body_men": {
                "name": "Full Body Laser (Men)",
                "name_ar": "ليزر كامل الجسم للرجال",
                "duration": 60,
                "price_omr": 130,
                "_placeholder_price": True,
            },
        },
    },
}


def get_service_duration(department: str, sub_service: str, units: int = 1) -> int:
    """Get duration in minutes for a specific service."""
    dept = SERVICES.get(department)
    if not dept:
        return 30
    svc = dept["sub_services"].get(sub_service)
    if not svc:
        return 30
    if "duration_per_unit" in svc:
        return svc["duration_per_unit"] * units
    return svc["duration"]


def get_capacity(department: str) -> int:
    """Get how many concurrent appointments a department supports."""
    dept = SERVICES.get(department)
    return dept["capacity"] if dept else 1


def requires_doctor_choice(department: str) -> bool:
    """Check if department requires client to choose a doctor."""
    dept = SERVICES.get(department)
    return "doctors" in dept if dept else False


def get_doctors(department: str) -> list[str]:
    """Get list of doctors for a department."""
    dept = SERVICES.get(department)
    return dept.get("doctors", []) if dept else []


def get_all_services_text() -> str:
    """Generate a formatted text of all services for the AI prompt, including
    bilingual names, duration, price, and staff (or technician) handling.
    """
    lines = []
    for dept_key, dept in SERVICES.items():
        header = f"\n### {dept['name']} / {dept.get('name_ar', '')} — capacity {dept['capacity']}"
        lines.append(header)

        # Staff block (singular doctor, list of doctors, or technician-only).
        if dept.get("doctors"):
            lines.append(
                f"   Doctors: {', '.join(dept['doctors'])} — caller chooses a doctor."
            )
        elif dept.get("doctor"):
            lines.append(
                f"   Doctor: {dept['doctor']} — mention when relevant, no choice needed."
            )
        else:
            lines.append(
                "   No doctor — performed by a trained technician. "
                "Do NOT ask the caller about a doctor or name the technician."
            )

        for svc_key, svc in dept["sub_services"].items():
            name_en = svc["name"]
            name_ar = svc.get("name_ar", "")
            price = svc.get("price_omr")
            price_unit = svc.get("price_unit", "")
            if "duration_per_unit" in svc:
                dur = f"{svc['duration_per_unit']} min per {svc['unit']}"
            else:
                dur = f"{svc['duration']} min"
            price_str = f"{price} OMR" + (f" {price_unit}" if price_unit else "")
            lines.append(
                f"   - [{svc_key}] {name_en} / {name_ar} — {dur}, ~{price_str}"
            )
    return "\n".join(lines)


def get_department_doctor(department: str) -> str | None:
    """Return the staff doctor for a department, or None for technician-only
    departments. For multi-doctor departments, returns None (caller chooses).
    """
    dept = SERVICES.get(department)
    if not dept:
        return None
    if "doctors" in dept:
        return None  # caller chooses
    return dept.get("doctor")


def get_service_price(department: str, sub_service: str) -> dict | None:
    """Return {'omr': int, 'unit': str} or None if the service isn't priced."""
    dept = SERVICES.get(department)
    if not dept:
        return None
    svc = dept["sub_services"].get(sub_service)
    if not svc or "price_omr" not in svc:
        return None
    return {"omr": svc["price_omr"], "unit": svc.get("price_unit", "")}


# Pre-paid multi-session packages. Client buys a bundle up front at a
# discount; each booking against a matching service deducts one session.
# `sub_service = "_any_"` makes the package usable for any sub_service
# within the department.
#
# Pricing here is also placeholder — calibrate against the real Lavora
# package card before going live.
PACKAGES: dict[str, dict] = {
    "laser_bikini_6": {
        "name_en": "Bikini Laser — 6 Sessions",
        "name_ar": "ليزر البكيني — 6 جلسات",
        "department": "laser_hair_removal",
        "sub_service": "bikini",
        "total_sessions": 6,
        "price_omr": 120.0,
        "regular_price_omr": 150.0,
        "validity_months": 12,
    },
    "laser_full_body_women_6": {
        "name_en": "Full Body Laser (Women) — 6 Sessions",
        "name_ar": "ليزر كامل الجسم للسيدات — 6 جلسات",
        "department": "laser_hair_removal",
        "sub_service": "full_body_women",
        "total_sessions": 6,
        "price_omr": 600.0,
        "regular_price_omr": 720.0,
        "validity_months": 12,
    },
    "botox_3": {
        "name_en": "Botox — 3 Areas",
        "name_ar": "بوتوكس — 3 مناطق",
        "department": "aesthetics",
        "sub_service": "botox",
        "total_sessions": 3,
        "price_omr": 400.0,
        "regular_price_omr": 450.0,
        "validity_months": 6,
    },
    "prp_4": {
        "name_en": "PRP — 4 Sessions",
        "name_ar": "بلازما الصفائح — 4 جلسات",
        "department": "regenerative",
        "sub_service": "prp",
        "total_sessions": 4,
        "price_omr": 600.0,
        "regular_price_omr": 720.0,
        "validity_months": 6,
    },
    "slimming_any_10": {
        "name_en": "Slimming Flex — 10 Sessions (any device)",
        "name_ar": "باقة التنحيف المرنة — 10 جلسات (أي جهاز)",
        "department": "slimming",
        "sub_service": "_any_",
        "total_sessions": 10,
        "price_omr": 700.0,
        "regular_price_omr": 900.0,
        "validity_months": 6,
    },
    "dermatology_chemical_peel_4": {
        "name_en": "Chemical Peel — 4 Sessions",
        "name_ar": "التقشير الكيميائي — 4 جلسات",
        "department": "dermatology",
        "sub_service": "chemical_peel",
        "total_sessions": 4,
        "price_omr": 300.0,
        "regular_price_omr": 360.0,
        "validity_months": 6,
    },
}


# Each department's bookings live in their own worksheet (tab) inside the
# main Google Sheet. The receptionist gets one clean per-dept view instead
# of having to filter a single mixed sheet.
DEPARTMENT_SHEET_NAMES = {
    "dermatology": "Dermatology",
    "aesthetics": "Aesthetics",
    "regenerative": "Regenerative",
    "slimming": "Slimming",
    "gynecology": "Gynecology",
    "laser_hair_removal": "Laser Hair Removal",
}


def get_package(code: str) -> dict | None:
    """Look up a package by its catalog code."""
    return PACKAGES.get(code)


def get_packages_catalog_text(language: str = "en") -> str:
    """Formatted catalog for display to the client."""
    name_key = "name_ar" if language == "ar" else "name_en"
    saved_lbl = "وفّر" if language == "ar" else "save"
    valid_lbl = "صالح" if language == "ar" else "valid"
    months_lbl = "شهر" if language == "ar" else "months"
    lines = []
    for code, pkg in PACKAGES.items():
        saved = pkg["regular_price_omr"] - pkg["price_omr"]
        lines.append(
            f"- [{code}] {pkg[name_key]} — {pkg['price_omr']:.0f} OMR "
            f"({saved_lbl} {saved:.0f} OMR, {valid_lbl} {pkg['validity_months']} {months_lbl})"
        )
    return "\n".join(lines)
