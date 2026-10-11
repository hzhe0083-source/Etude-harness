# P5 offline scorer image (design §2 P5, §3.5 "改打分或汇总").
# The image holds only a Python runtime; harness code, RoboDojo and the eval
# results are bind-mounted READ-ONLY, the only writable mount is the output dir.
#
#   docker build -t etude-scorer scorer/
#   docker run --rm --read-only --tmpfs /tmp \
#     -v "$PWD":/harness:ro \
#     -v "$HOME/Documents/RoboDojo":/robodojo:ro \
#     -v "$HOME/Documents/RoboDojo/eval_result/RoboDojo":/eval:ro \
#     -v "$PWD/gate/out":/out \
#     etude-scorer --eval-root /eval --task stack_bowls --task put_bottles_into_dustbin \
#       --expect-summarize-sha <sha from the incumbent receipt>
FROM python:3.12-slim
RUN pip install --no-cache-dir numpy==2.1.3 pyyaml==6.0.2 jsonschema==4.23.0 \
 && useradd --create-home --uid 1000 scorer
ENV ROBODOJO=/robodojo OUT_DIR=/out PYTHON=python3 PYTHONDONTWRITEBYTECODE=1
USER scorer
WORKDIR /tmp
ENTRYPOINT ["bash", "/harness/scorer/run_official.sh"]
