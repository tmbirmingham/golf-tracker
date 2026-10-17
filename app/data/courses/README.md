# Course data

Each `*.json` file here is one course's reference data: details, hole pars and
handicaps, and tees with per-hole yardages. Files are loaded into the SQLite
database when the app starts, but only for courses that aren't in the database
yet. The app never scrapes course websites.

Every field carries a provenance status (`provenance` for course fields,
`status` per tee):

- `verified`: checked against the course's official source (give `source` and `checked_on`)
- `unverified`: from a secondary source, not yet confirmed
- `user_entered`: entered by hand by the app's user

Unverified and user-entered data is labeled in the app during a round.

## Adding or correcting data

1. Edit the JSON. Add tees under `tees`, for example:

   ```json
   {
     "name": "Blue", "color": "blue", "gender": "M",
     "status": "verified", "source": "<official scorecard URL>",
     "course_rating": null, "slope_rating": null,
     "published_par": 72, "published_yardage": null,
     "holes": [{"number": 1, "yardage": null}]
   }
   ```

   Set `published_par` and `published_yardage` to the totals printed on the
   scorecard, so the hole-by-hole numbers are checked against them.
2. Run `flask courses validate`. It checks hole numbering, pars, handicaps,
   ratings, and that pars and yardages add up to the published totals.
3. Run `flask courses import --update` to apply the changes to an existing
   database. This upserts the course, its holes and its tees and never deletes
   anything. Saved rounds keep their own snapshot of the course as it was
   played, so their scores and to-par figures don't change.
