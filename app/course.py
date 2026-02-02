"""
Course configuration - update with your local course details
"""

COURSE_NAME = "Arrowhead Golf Course"
COURSE_DESCRIPTION = "Arrowhead Golf Course, Naples, FL."

# Par for each hole (1-18)
HOLE_PARS = {
    1: 5,
    2: 4,
    3: 4,
    4: 3,
    5: 5,
    6: 4,
    7: 4,
    8: 3,
    9: 4,
    10: 4,
    11: 3,
    12: 4,
    13: 5,
    14: 4,
    15: 3,
    16: 4,
    17: 4,
    18: 5,
}

def get_hole_par(hole):
    """Get par for a specific hole (1-18)"""
    return HOLE_PARS.get(hole, 4)

def is_par_3(hole):
    """Check if a hole is a par 3"""
    return get_hole_par(hole) == 3

def get_course_info():
    """Get course information"""
    return {
        "name": COURSE_NAME,
        "description": COURSE_DESCRIPTION,
        "total_par": sum(HOLE_PARS.values()),
        "holes": HOLE_PARS
    }
