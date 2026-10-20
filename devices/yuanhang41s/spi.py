"""Accept the SPI core's default zero word delay on the SPRD controller."""
from pathlib import Path


def apply(kernel):
    path = Path(kernel) / 'drivers/spi/spi-sprd.c'
    text = path.read_text()
    old = 'if (d->unit != SPI_DELAY_UNIT_SCK)'
    new = 'if (d->value && d->unit != SPI_DELAY_UNIT_SCK)'
    if new not in text:
        if text.count(old) != 1:
            raise ValueError('Unexpected SPRD word-delay validation')
        # A delay of zero has no unit-dependent conversion. Rejecting the
        # core's default USECS/0 prevents even the first five-byte command.
        text = text.replace(old, new)
    start = text.index('static int sprd_spi_txrx_bufs(')
    end = text.index('static void sprd_spi_irq_enable(', start)
    body = text[start:end]
    if 'factory full-duplex RX completion' not in body:
        old = '\t\t\tsprd_spi_set_tx_length(ss, len);'
        if body.count(old) != 1:
            raise ValueError('Unexpected PIO TX length setup')
        body = body.replace(old, '''\t\t\t/* factory full-duplex RX completion: both counters must be set. */
\t\t\tif (ss->trans_mode & SPRD_SPI_RX_MODE)
\t\t\t\tsprd_spi_set_rx_length(ss, len);
''' + old)
        old = '\t\t\tret = sprd_spi_wait_for_tx_end(ss, t);'
        if body.count(old) != 1:
            raise ValueError('Unexpected PIO completion wait')
        body = body.replace(old, '''\t\t\t/* TX_END alone does not guarantee that RX FIFO is complete. */
\t\t\tret = (ss->trans_mode & SPRD_SPI_RX_MODE) ?
\t\t\t\tsprd_spi_wait_for_rx_end(ss, t) :
\t\t\t\tsprd_spi_wait_for_tx_end(ss, t);''')
        text = text[:start] + body + text[end:]
    start = text.index('static int sprd_spi_init_hw(')
    end = text.index('static int sprd_spi_setup_transfer(', start)
    body = text[start:end]
    if 'factory transfer interrupt reset' not in body:
        body = body.replace('\treturn 0;', '''\t/* factory transfer interrupt reset: a duplex read leaves TX_END set. */
\twritel_relaxed(0x33f, ss->base + SPRD_SPI_INT_CLR);
\treturn 0;''')
        text = text[:start] + body + text[end:]
    path.write_text(text)


if __name__ == '__main__':
    import sys
    apply(sys.argv[1])
