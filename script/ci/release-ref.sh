#!/usr/bin/env bash
# release-ref.sh - the ONE classifier of a git ref this project releases
# from.
#
# Provenance: a verbatim copy of script/ci/release-ref.sh in
# ycpss91255-docker/base, which owns the rule. It travels with any repo
# that publishes an image on tag, because a workflow cannot reach into
# another repository for a script. The rationale below is base's and
# names base's workflows; they are the history of why this refuses
# rather than guesses, not files you will find here.
#
# Usage:
#   release-ref.sh prerelease <ref>   print "true" or "false"
#
# <ref> is a bare tag (`v0.42.0-rc4`, i.e. github.ref_name) or a full ref
# (`refs/tags/v0.42.0-rc4`, i.e. GITHUB_REF). A caller passes whichever
# it holds; both answer the same.
#
# ── Why the question needs a home ────────────────────────────────────────
#
# "Is this tag a prerelease?" decides two things, and both are consumed
# across the whole org:
#
#   release-worker.yaml       whether the GitHub Release cut for a
#   self-test.yaml            downstream repo -- or for base itself -- is
#                             marked prerelease
#   release-test-tools.yaml   whether the published tooling image moves
#                             `test-tools:latest`, which is the image
#                             every repo that has not pinned
#                             `test_tools_version` builds its lint stage
#                             from, that input's default being "latest"
#
# The first two spelled it `contains(github.ref_name, '-')`. The third
# did not ask at all and moved `:latest` on v0.42.0-rc1 through -rc4, so
# for the length of an RC window the org linted against a release
# candidate. Repairing only the third would have left three hand-kept
# copies of one rule and nothing in the tree comparing any pair of them --
# the same shape as the four unpinned `just` provenance paths that
# dist/script/base/just-version.sh exists to collapse.
#
# ── Why it refuses rather than guesses ───────────────────────────────────
#
# `contains(github.ref_name, '-')` is true of `feature/add-thing` and of
# `release-prep`. Those are not prereleases; they are not versions. A
# predicate that answers a question about an input it cannot parse is
# guessing, and here the guess it makes -- `false` -- is the branch that
# PUBLISHES: it marks a Release final and moves the org's `:latest`.
#
# So a ref this cannot read as a version tag is refused, loudly, with the
# ref in the message. Same direction as ghcr-cleanup.yaml's dry-run
# resolver, which routes an input it cannot parse to the safe branch
# rather than the default one, and the same as just-version.sh, which
# fails rather than falling back to "whatever is latest".
#
# Reads nothing from the environment: the ref is the whole input.

# Only set strict mode when running directly; when sourced, respect the
# caller's settings.
if [[ "${BASH_SOURCE[0]:-}" == "${0:-}" ]]; then
  set -euo pipefail
fi

# SemVer 2.0.0, with the leading `v` this project's tags carry made
# optional so github.ref_name and a bare version both parse. The capture
# that decides the answer is group 5: the prerelease identifiers of §9,
# WITHOUT the `-` that introduces them, so an empty group and an absent
# group are the same "no prerelease part".
#
# Build metadata (§10) is matched and ignored on purpose: `+build.5` says
# nothing about precedence, so `v1.0.0+build.5` is a finished release. A
# pattern that stopped at the prerelease group would have refused it
# instead, which is the fail-loud direction but for a tag that is legal.
_RR_SEMVER_RE='^v?([0-9]+)\.([0-9]+)\.([0-9]+)(-([0-9A-Za-z.-]+))?(\+([0-9A-Za-z.-]+))?$'

# _rr_prerelease <ref> -- print "true" / "false", or refuse.
_rr_prerelease() {
  local _ref="${1-}" _tag
  if [[ -z "${_ref}" ]]; then
    echo "release-ref: prerelease needs a ref (a tag such as v0.42.0-rc4, or refs/tags/v0.42.0-rc4). It has no default: a missing ref would otherwise be answered 'false', which is the branch that publishes." >&2
    return 2
  fi
  _tag="${_ref#refs/tags/}"
  if [[ ! "${_tag}" =~ ${_RR_SEMVER_RE} ]]; then
    echo "release-ref: '${_ref}' is not a version tag, so whether it is a prerelease is not a question this can answer. Expected <major>.<minor>.<patch> with an optional leading 'v', an optional -prerelease and an optional +build." >&2
    return 2
  fi
  if [[ -n "${BASH_REMATCH[5]}" ]]; then
    printf 'true\n'
  else
    printf 'false\n'
  fi
}

# _rr_main <subcommand> [arg]... -- dispatch. An unrecognised subcommand
# is refused and the message names what IS answered, rather than falling
# through to the one question that exists today: a caller that asked for
# something else asked for a reason.
_rr_main() {
  local _cmd="${1-}"
  case "${_cmd}" in
    prerelease) shift; _rr_prerelease "${@}" ;;
    *)
      echo "release-ref: no such question '${_cmd}'. This answers: prerelease <ref> -- print 'true' when <ref> is a prerelease version tag, 'false' when it is a finished one." >&2
      return 2
      ;;
  esac
}

# Only run when executed directly, not when sourced (for testing).
if [[ "${BASH_SOURCE[0]:-}" == "${0:-}" ]]; then
  _rr_main "$@"
fi
