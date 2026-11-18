# Boot filesystem initialization and the standard rp2 helper, without network libraries.
freeze("$(PORT_DIR)/modules", ("_boot.py", "rp2.py"))
