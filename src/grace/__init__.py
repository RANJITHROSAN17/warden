"""Grace - the agent that finishes the payment.

A payment is authorised, then it waits. In that window the money is committed, the customer is
already waiting for an answer, and the capture can still fail. Grace is the layer that decides what
happens next - inside the authority that allowed the payment in the first place - and leaves a
signed receipt for every decision, including the refusals.
"""

__version__ = "0.1.0"
