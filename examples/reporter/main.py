"""An example goxo agent built with the oxo SDK.

It logs a banner on start, then for each scanned IP asset emits a vulnerability
report. It is a shape demonstration, not a real scanner.
"""

import oxo

agent = oxo.Agent()


@agent.on_start
def start(ctx: oxo.Context) -> None:
    ctx.log.info(
        "reporter starting: agent=%s universe=%s",
        ctx.identity.agent,
        ctx.identity.universe,
    )


@agent.on_message("v3.asset.ip")
def report(ctx: oxo.Context, msg: oxo.Message) -> None:
    host = msg.data.get("host", "")
    ctx.log.info("scanning asset: host=%s", host)
    ctx.emit(
        "v3.report.vuln",
        {
            "title": "host reachable",
            "risk_rating": "INFO",
            "technical_detail": f"host {host} responded",
        },
    )


if __name__ == "__main__":
    agent.run()
