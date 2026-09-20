import logging
import random
import time
from dataclasses import dataclass


logger = logging.getLogger(__name__)


@dataclass
class ReplyChain:
    cap: int | None = None
    replies_sent: int = 0
    generation_in_flight: bool = False
    last_activity: float = 0.0


class ReplyChainTracker:
    def __init__(
        self,
        caps: tuple[int, ...],
        weights: tuple[int, ...],
        label: str,
        ttl_seconds: float = 1800.0,
    ):
        if not caps or len(caps) != len(weights):
            raise ValueError("Reply-chain caps and weights must have matching values.")

        self.caps = caps
        self.weights = weights
        self.label = label
        self.ttl_seconds = ttl_seconds
        self.chains: dict[int, ReplyChain] = {}
        self.message_to_chain: dict[int, int] = {}

    def register_incoming(
        self,
        message_id: int,
        referenced_message_id: int | None,
    ) -> int:
        now = time.monotonic()
        self._remove_expired(now)

        chain_id = self.message_to_chain.get(referenced_message_id)
        if chain_id not in self.chains:
            chain_id = message_id
            self.chains[chain_id] = ReplyChain(last_activity=now)

        self.message_to_chain[message_id] = chain_id
        self.chains[chain_id].last_activity = now
        return chain_id

    def try_reserve_generation(self, chain_id: int) -> bool:
        chain = self.chains.get(chain_id)
        if chain is None:
            return False

        if chain.cap is None:
            chain.cap = random.choices(self.caps, weights=self.weights, k=1)[0]
            logger.info(
                "%s reply chain %s rolled a cap of %s.",
                self.label,
                chain_id,
                chain.cap,
            )

        chain.last_activity = time.monotonic()

        if chain.generation_in_flight or chain.replies_sent >= chain.cap:
            return False

        chain.generation_in_flight = True
        return True

    def release_generation(self, chain_id: int) -> None:
        chain = self.chains.get(chain_id)
        if chain is None:
            return

        chain.generation_in_flight = False
        chain.last_activity = time.monotonic()

    def record_reply(
        self,
        chain_id: int,
        reply_message_id: int,
    ) -> tuple[int, int] | None:
        chain = self.chains.get(chain_id)
        if chain is None or chain.cap is None:
            return None

        chain.generation_in_flight = False
        chain.replies_sent += 1
        chain.last_activity = time.monotonic()
        self.message_to_chain[reply_message_id] = chain_id
        return chain.replies_sent, chain.cap

    def _remove_expired(self, now: float) -> None:
        expired_chain_ids = {
            chain_id
            for chain_id, chain in self.chains.items()
            if now - chain.last_activity > self.ttl_seconds
        }

        for chain_id in expired_chain_ids:
            del self.chains[chain_id]

        if expired_chain_ids:
            self.message_to_chain = {
                message_id: chain_id
                for message_id, chain_id in self.message_to_chain.items()
                if chain_id not in expired_chain_ids
            }