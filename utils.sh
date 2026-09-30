#!/bin/bash
# Shared colors and helpers

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

step()      { echo -e "\n${GREEN}[*]${NC} $1"; }
ok()        { echo -e "  ${GREEN}[ok]${NC} $1"; }           # already present, no change
installed() { echo -e "  ${GREEN}[+]${NC} $1"; }            # just installed/deployed
skip()      { echo -e "  ${YELLOW}[--]${NC} $1"; }
fail()      { echo -e "  ${RED}[x]${NC} $1"; }
