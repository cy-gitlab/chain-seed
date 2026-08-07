import unittest


class PublicImportTests(unittest.TestCase):
    def test_extension_descriptor(self):
        from chainseed import descriptor

        self.assertEqual(descriptor.distribution, "chain-seed")
        self.assertEqual(descriptor.extension_api_version, 1)

    def test_chain_public_imports(self):
        from chainseed.chainlink import ChainlinkService
        from chainseed.ethereum import ConsensusMechanism, EthExplorer, EthereumService
        from chainseed.monero import MoneroService
        from chainseed.solana import SolanaService

        self.assertEqual(ConsensusMechanism.POS.value, "POS")
        self.assertTrue(
            all(
                service is not None
                for service in (
                    EthereumService,
                    EthExplorer,
                    MoneroService,
                    SolanaService,
                    ChainlinkService,
                )
            )
        )

    def test_protocol_aware_test_runner_import(self):
        from chainseed.testing import BlockchainTestRunner, EthereumRuntimeTest

        self.assertEqual(BlockchainTestRunner.runner_name, "blockchain")
        self.assertIsNotNone(EthereumRuntimeTest)


if __name__ == "__main__":
    unittest.main()
