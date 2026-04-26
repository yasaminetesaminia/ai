"""
Noora Clinic — Service Definitions

Each department has:
- capacity: how many clients can be served simultaneously
- doctors: (optional) list of doctors, client must choose one
- sub_services: specific treatments with their duration in minutes
"""

SERVICES = {
    "dentistry": {
        "name": "Dentistry",
        "name_ar": "طب الأسنان",
        "capacity": 1,
        "doctor": "Dr. Sara",  # staff dentist — mentioned to callers when relevant
        "sub_services": {
            "checkup": {"name": "Dental Checkup", "name_ar": "فحص أسنان", "duration": 20, "price_omr": 10},
            "filling": {"name": "Dental Filling", "name_ar": "حشو أسنان", "duration": 35, "price_omr": 30},
            "root_canal": {"name": "Root Canal Treatment", "name_ar": "علاج جذور", "duration": 45, "price_omr": 90},
            "veneer": {
                "name": "Dental Veneer (Laminate)",
                "name_ar": "تلبيس أسنان (لامينيت)",
                "duration_per_unit": 10,
                "unit": "tooth",
                "price_omr": 100,  # per tooth
                "price_unit": "per tooth",
            },
            "implant": {"name": "Dental Implant", "name_ar": "زراعة أسنان", "duration": 75, "price_omr": 300},
        },
    },
    "laser_hair_removal": {
        "name": "Laser Hair Removal",
        "name_ar": "إزالة الشعر بالليزر",
        "capacity": 4,
        "doctor": None,  # technician-only — don't ask or name them to callers
        "sub_services": {
            "bikini": {"name": "Bikini Laser", "name_ar": "ليزر البكيني", "duration": 15, "price_omr": 20},
            "full_body_partial": {
                "name": "Full Body Laser (excluding back & abdomen)",
                "name_ar": "ليزر الجسم (بدون الظهر والبطن)",
                "duration": 45,
                "price_omr": 75,
            },
            "full_body": {"name": "Full Body Laser", "name_ar": "ليزر كامل الجسم", "duration": 60, "price_omr": 100},
        },
    },
    "slimming": {
        "name": "Slimming & Body Contouring",
        "name_ar": "التنحيف ونحت الجسم",
        "capacity": 2,
        "doctor": "Dr. Enas",  # slimming physician
        "sub_services": {
            "schwarzy": {"name": "Schwarzy", "name_ar": "شفارزي", "duration": 60, "price_omr": 60},
            "onda_plus": {"name": "Onda Plus", "name_ar": "أوندا بلس", "duration": 60, "price_omr": 70},
            "radiosteam": {"name": "Radiosteam", "name_ar": "راديو ستيم", "duration": 60, "price_omr": 50},
        },
    },
    "beauty": {
        "name": "Beauty & Aesthetics",
        "name_ar": "التجميل",
        "capacity": 2,
        "doctors": ["Dr. Amani", "Dr. Hossein"],  # caller chooses
        "sub_services": {
            "skin_lift": {"name": "Skin Lift", "name_ar": "شد البشرة", "duration": 20, "price_omr": 60},
            "laser_spots_wrinkles": {
                "name": "Laser Treatment (spots & wrinkles)",
                "name_ar": "ليزر للبقع والتجاعيد",
                "duration": 20,
                "price_omr": 80,
            },
            "botox": {"name": "Botox", "name_ar": "بوتوكس", "duration": 20, "price_omr": 120, "price_unit": "per session"},
            "filler": {"name": "Filler", "name_ar": "فيلر", "duration": 20, "price_omr": 150, "price_unit": "per syringe"},
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
# within the department (e.g., flexible laser package).
PACKAGES: dict[str, dict] = {
    "laser_bikini_6": {
        "name_en": "Bikini Laser — 6 Sessions",
        "name_ar": "ليزر البكيني — 6 جلسات",
        "department": "laser_hair_removal",
        "sub_service": "bikini",
        "total_sessions": 6,
        "price_omr": 90.0,
        "regular_price_omr": 120.0,
        "validity_months": 12,
    },
    "laser_bikini_10": {
        "name_en": "Bikini Laser — 10 Sessions",
        "name_ar": "ليزر البكيني — 10 جلسات",
        "department": "laser_hair_removal",
        "sub_service": "bikini",
        "total_sessions": 10,
        "price_omr": 140.0,
        "regular_price_omr": 200.0,
        "validity_months": 12,
    },
    "laser_full_body_6": {
        "name_en": "Full Body Laser — 6 Sessions",
        "name_ar": "ليزر كامل الجسم — 6 جلسات",
        "department": "laser_hair_removal",
        "sub_service": "full_body",
        "total_sessions": 6,
        "price_omr": 300.0,
        "regular_price_omr": 420.0,
        "validity_months": 12,
    },
    "slimming_schwarzy_8": {
        "name_en": "Schwarzy Body Sculpting — 8 Sessions",
        "name_ar": "نحت الجسم شفارزي — 8 جلسات",
        "department": "slimming",
        "sub_service": "schwarzy",
        "total_sessions": 8,
        "price_omr": 320.0,
        "regular_price_omr": 480.0,
        "validity_months": 6,
    },
    "slimming_any_10": {
        "name_en": "Slimming Flex — 10 Sessions (any device)",
        "name_ar": "باقة التنحيف المرنة — 10 جلسات (أي جهاز)",
        "department": "slimming",
        "sub_service": "_any_",
        "total_sessions": 10,
        "price_omr": 380.0,
        "regular_price_omr": 600.0,
        "validity_months": 6,
    },
    "beauty_skin_lift_3": {
        "name_en": "Skin Lift — 3 Sessions",
        "name_ar": "شد البشرة — 3 جلسات",
        "department": "beauty",
        "sub_service": "skin_lift",
        "total_sessions": 3,
        "price_omr": 150.0,
        "regular_price_omr": 180.0,
        "validity_months": 6,
    },
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
