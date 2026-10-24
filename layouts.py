from model import Region, SetLayout


SET_LAYOUTS = {

    1: SetLayout(

        start_rotation=Region(
            row1=10,
            row2=10,
            col1=5,
            col2=10
        ),

        serve_rounds=Region(
            row1=20,
            row2=23,
            col1=5,
            col2=10
        ),

        substitutions=Region(
            row1=13,
            row2=16,
            col1=5,
            col2=10
        ),

        own_score=(10, 11),

        opp_score=(10, 24)
    ),

    2: SetLayout(
        start_rotation=Region(
            row1=10,
            row2=10,
            col1=30,
            col2=35
        ),

        serve_rounds=Region(
            row1=20,
            row2=23,
            col1=30,
            col2=35
        ),

        substitutions=Region(
            row1=13,
            row2=16,
            col1=30,
            col2=35
        ),

        own_score=(10, 36),

        opp_score=(10, 49)
    ),

    3: SetLayout(
        start_rotation=Region(
            row1=50,
            row2=50,
            col1=5,
            col2=10
        ),

        serve_rounds=Region(
            row1=60,
            row2=63,
            col1=5,
            col2=10
        ),

        substitutions=Region(
            row1=53,
            row2=56,
            col1=5,
            col2=10
        ),

        own_score=(50, 11),

        opp_score=(50, 24)
    ),

    4: SetLayout(
        start_rotation=Region(
            row1=50,
            row2=50,
            col1=30,
            col2=35
        ),

        serve_rounds=Region(
            row1=60,
            row2=63,
            col1=30,
            col2=35
        ),

        substitutions=Region(
            row1=53,
            row2=56,
            col1=30,
            col2=35
        ),

        own_score=(50, 36),

        opp_score=(50, 49)
    )
}
