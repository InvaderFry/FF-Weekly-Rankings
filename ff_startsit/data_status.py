"""Run metadata shared by reports and dashboards; never a scoring input."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape


@dataclass
class DataStatus:
    season: int
    week: int
    expected: list[str]
    included: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    details: list[str] = field(default_factory=list)

    def lines(self):
        lines = [f"Season {self.season} · Week {self.week} · Generated {self.generated_at}",
                 f"Leagues: {len(self.included)} of {len(self.expected)} included" +
                 (" — INCOMPLETE" if self.skipped else ""),
                 "Included: " + (", ".join(self.included) or "none")]
        lines += [f"Skipped {name}: {reason}" for name, reason in self.skipped.items()]
        lines += self.details
        lines.append("Generation time is not a provider update time. Source fetch/update times are unavailable unless stated.")
        return lines

    def markdown(self):
        from .output.render import md_cell
        return "## Data status\n\n" + "\n".join(f"- {md_cell(line)}" for line in self.lines()) + "\n"

    def html(self):
        return "<section><h2>Data status</h2><ul>" + "".join(
            f"<li>{escape(line)}</li>" for line in self.lines()) + "</ul></section>"


def bundle_status(bundles):
    return next((b.data_status for b in bundles if getattr(b, "data_status", None)), None)


def finish_status(status, bundles):
    """Count actual readings, retaining the signal's own unavailable reason."""
    from collections import Counter
    for bundle in bundles:
        status.included.append(bundle.label)
        bundle.data_status = status
        recs = getattr(bundle, "recs", {})
        missing = Counter()
        total = Counter()
        for rec in recs.values():
            for detail in rec.source_status:
                line = f"{bundle.label}: {detail}"
                if line not in status.details:
                    status.details.append(line)
            for score in rec.scores:
                for name in rec.weights:
                    total[name] += 1
                    value = score.raw.get(name)
                    if value is None or not value.available or value.raw is None:
                        missing[(name, (value.note or "unavailable") if value else "no reading")] += 1
        for (name, reason), count in sorted(missing.items()):
            status.details.append(f"{bundle.label}: {name} missing {count}/{total[name]} player readings ({reason})")
        if hasattr(bundle, "coverage"):
            for name, count in sorted(bundle.coverage.items()):
                status.details.append(f"{bundle.label}: {name} covers {count}/{bundle.pool_size} free agents")
            status.details.extend(
                f"{bundle.label}: {note}" for note in bundle.notes + bundle.league_notes
                if "unavailable" in note.lower() or "column" in note.lower())
    status.generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return bundles


def single_status(week, label, recs):
    from types import SimpleNamespace
    from .season import season_year
    label = label or "default"
    status = DataStatus(season_year(), week, [label])
    finish_status(status, [SimpleNamespace(label=label, recs=recs)])
    return status
