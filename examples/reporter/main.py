"""An example goxo agent built with the oxo SDK.

For each scanned IP asset it emits a vulnerability report. It is a shape
demonstration, not a real scanner.
"""

import oxo

agent = oxo.Agent()


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
