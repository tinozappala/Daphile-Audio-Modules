Modules/_dbm$(EXT_SUFFIX): Modules/_dbmmodule.o
	$(BLDSHARED) $< $(RC5_DBM_LIB) $(MODULE_LDFLAGS_SHARED) -o $@

Modules/_dbmmodule.o: $(srcdir)/Modules/_dbmmodule.c $(PYTHON_HEADERS)
	$(CC) -DUSE_GDBM_COMPAT -DHAVE_GDBM_DASH_NDBM_H=1 $(PY_STDMODULE_CFLAGS) $(CCSHARED) -c $< -o $@
